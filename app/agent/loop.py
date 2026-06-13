"""The bounded agent loop — drive Groq's native tool-calling within hard iteration + token caps.

This is the only place the LLM is given the tools (`app/agent/tools.py`) and allowed to act. The loop is
deliberately small and bounded (Constitution VI / FR-026): it calls Groq with the tool specs, runs any
tool calls the model emits (each validated + wall-guarded inside `tools.dispatch`), feeds the results
back, and repeats until the model stops calling tools OR a bound is hit — `agent_max_iterations` rounds
or the cumulative `agent_token_budget`. There is no path to an unbounded loop and no path around the wall.

It returns a `LoopOutcome` carrying the model's closing text AND the `ToolContext` whose `surfaced` dict
holds every wall-cleared recipe row the tools produced. The meal-plan service reads those rows to assemble
the plan deterministically, so even if the model is cut off mid-thought (bound reached), the work it
already did survives as the "best safe partial result".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.agent import tools
from app.agent.tools import ToolContext
from app.config import get_settings
from app.infra import llm
from app.services.user.constraint_guard import ConstraintProfile

log = structlog.get_logger()

# The agent's framing prompt (prompts are code — golden rule #7). Loaded per run so an edit takes effect.
_AGENT_PROMPT = Path("prompts/agent_system.md")

# Per-call output cap. The agent's turns are mostly short tool calls, so a modest cap keeps each round
# cheap; the cumulative `agent_token_budget` is the real spend ceiling enforced across rounds below.
_PER_CALL_MAX_TOKENS = 700

# How many times to attempt one round's model call. The agent model occasionally emits a malformed or
# mistyped tool call that Groq rejects with a 400 (`tool_use_failed`); since generation is stochastic, a
# single immediate retry usually produces a well-formed call.
_CALL_ATTEMPTS = 2


def _call_model(messages: list[dict[str, Any]], settings: Any) -> Any | None:
    """Call the active LLM with the tool specs for one round, retrying once on failure; None on failure.

    Wraps `llm.chat` so a provider/tool-call error no longer dies silently: every failure is logged
    (so a reproducible break is visible in operations, not swallowed) and retried once, because a malformed
    tool call from the stochastic model often comes out well-formed on a second try. Returns the response,
    or None after `_CALL_ATTEMPTS` failures — the signal for the loop to stop with the best safe partial.
    The agent model is resolved per ACTIVE provider via `settings.agent_model` (the 005 seam), so a provider
    swap never sends one provider's model id to the other.
    """
    for attempt in range(_CALL_ATTEMPTS):
        try:
            return llm.chat(
                messages,
                tools=tools.TOOL_SPECS,
                model=settings.agent_model,
                max_tokens=_PER_CALL_MAX_TOKENS,
            )
        except Exception as exc:  # noqa: BLE001 — log + retry once, then end the loop gracefully
            log.warning("agent.model_call_failed", attempt=attempt, error=str(exc))
    return None


@dataclass
class LoopOutcome:
    """One bounded-agent run's result: the model's closing text and the context holding surfaced rows.

    `text` is the model's final natural-language message (empty when a bound cut it off before it spoke).
    `ctx.surfaced` is the real, wall-cleared recipe rows the tools returned — the substrate the meal-plan
    service assembles into the plan, so a truncated run still yields the best safe partial result.
    """

    text: str
    ctx: ToolContext


def _usage_tokens(response: Any) -> int:
    """Read the cumulative token count from a Groq response, defaulting to 0 when usage is absent.

    Used to advance the running total against `agent_token_budget`; a mocked response without `usage`
    contributes 0 so tests exercise the iteration bound without needing to fake token accounting.
    """
    usage = getattr(response, "usage", None)
    return int(getattr(usage, "total_tokens", 0) or 0)


def _assistant_history_entry(message: Any, tool_calls: Any) -> dict[str, Any]:
    """Rebuild the assistant turn (content + its tool calls) as a plain dict for the message history.

    Groq's native tool-calling requires the assistant message that REQUESTED the tools to precede the
    tool-result messages in the next request, each linked by `tool_call_id`. This serializes the SDK's
    message object back into that wire shape so the conversation stays well-formed across rounds.
    """
    return {
        "role": "assistant",
        "content": getattr(message, "content", None) or "",
        "tool_calls": [
            {
                "id": getattr(call, "id", ""),
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments or "{}",
                },
            }
            for call in tool_calls
        ],
    }


def run(
    session: Session,
    message: str,
    cp: ConstraintProfile,
    profile_id: str,
    servings: int,
) -> LoopOutcome:
    """Run the bounded tool-calling loop for one turn and return its text + surfaced recipe context.

    Seeds the conversation with the agent system prompt and the cook's message, then loops: call Groq
    (with the tool specs and the stronger agent model), and if the model emits tool calls, dispatch each
    through `tools.dispatch` (validation + wall inside) and feed the JSON results back as `tool` messages
    before the next round. The loop ends when the model answers without calling a tool, or when a bound is
    reached — `agent_max_iterations` rounds or the cumulative token budget — whichever comes first. Any
    provider error ends the loop gracefully with whatever was gathered so far (best safe partial, never a
    crash). The cook's constraints/servings live only in the trusted `ToolContext`, never in the prompt.
    """
    settings = get_settings()
    ctx = ToolContext(session=session, cp=cp, profile_id=profile_id, servings=servings)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _AGENT_PROMPT.read_text(encoding="utf-8")},
        {"role": "user", "content": message},
    ]
    tokens_used = 0
    final_text = ""

    for _ in range(settings.agent_max_iterations):
        if tokens_used >= settings.agent_token_budget:
            break  # token bound hit — stop and return the best safe partial gathered so far
        response = _call_model(messages, settings)
        if response is None:
            break  # the model call failed twice (logged) — end gracefully with the best safe partial

        tokens_used += _usage_tokens(response)
        choice_message = response.choices[0].message
        tool_calls = getattr(choice_message, "tool_calls", None)
        if not tool_calls:
            # The model answered without calling a tool → that is its final word; we are done.
            final_text = (getattr(choice_message, "content", None) or "").strip()
            break

        # Record the assistant's tool-call request, then run each call and feed its result back.
        messages.append(_assistant_history_entry(choice_message, tool_calls))
        for call in tool_calls:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except (TypeError, ValueError):
                arguments = {}  # malformed args → empty dict → the tool's validation rejects it cleanly
            result = tools.dispatch(call.function.name, arguments, ctx)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": getattr(call, "id", ""),
                    "content": json.dumps(result, default=str),
                }
            )

    return LoopOutcome(text=final_text, ctx=ctx)
