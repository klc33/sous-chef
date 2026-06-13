"""The router — turn a classified intent into a dispatch decision (workflow | agent | refuse).

Every turn flows through here right after the input rail. `route(message)` calls the trained classifier
(`app/classifier/predict.py`) and maps the label + confidence to one of three handlers per
contracts/classifier.md:
  * zero-signal (no known vocabulary matched) → clarify (cheap re-prompt; NEVER the agent — FR-004a)
  * `plan_meals`                          → agent (the multi-step, multi-tool path)
  * confidence < `router_confidence_threshold` WITH real signal (and NOT find_recipe) → agent (escalate)
  * `find_recipe`                         → workflow ALWAYS (semantic search is its home at any confidence)
  * `out_of_scope`                        → refuse (safe canned redirect)
  * everything else                       → workflow (deterministic handler)

The zero-signal check runs FIRST and short-circuits to a cheap clarification: a message that matches no
known vocabulary gives the agent nothing to act on, so spending an expensive agent call on it is waste
(FR-004a). Only genuinely-ambiguous turns — low confidence yet with real matched signal — escalate to the
agent. Either way a misclassification degrades cost/quality, never safety: every path still passes the
wall + output rail (FR-004).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any, Literal

from app.classifier import predict as classifier
from app.config import get_settings

# "clarify" is a cheap deterministic re-prompt for zero-signal turns; "refuse" is the safe out_of_scope
# redirect. Both stay on the deterministic path — neither reaches the agent.
Route = Literal["workflow", "agent", "refuse", "clarify"]

# Intent label assigned to a zero-signal turn (no real classification happened — see FR-004a).
LOW_SIGNAL_INTENT = "low_signal"

# Redis keys for the workflow-vs-agent routing split the operator dashboard reads (004-evals-and-uis,
# FR-026). Only the hard `agent` fork counts as "agent"; every deterministic path (workflow / refuse /
# clarify) counts as "workflow", so the split mirrors the turn fork the metric is about.
ROUTING_COUNTER_AGENT = "routing:agent"
ROUTING_COUNTER_WORKFLOW = "routing:workflow"


def record_decision(cache: Any, route: Route) -> None:
    """Increment the workflow-vs-agent routing counter for one routing decision — best-effort, never fatal.

    The operator dashboard derives its routing split from these two Redis counters (no new table). The
    `agent` fork bumps `routing:agent`; every deterministic route bumps `routing:workflow`. Metrics must
    never break a cook's turn, so a missing cache or any Redis error is swallowed silently (the counter is
    observability, not correctness) — exactly the posture tracing takes.
    """
    if cache is None:
        return
    key = ROUTING_COUNTER_AGENT if route == "agent" else ROUTING_COUNTER_WORKFLOW
    # A metrics counter must never propagate into the turn path — swallow any Redis hiccup, like tracing.
    with contextlib.suppress(Exception):
        cache.client.incr(key)


@dataclass(frozen=True)
class IntentRoute:
    """The routing decision for one turn: the predicted intent, its confidence, and the chosen handler."""

    intent: str
    confidence: float
    route: Route


def route(message: str) -> IntentRoute:
    """Classify `message` and decide which handler should serve the turn.

    Order matters: a zero-signal turn (the classifier matched no known vocabulary) is caught FIRST and
    sent to a cheap `clarify` re-prompt — it never reaches the agent (FR-004a). Otherwise, a low-confidence
    turn *with* real signal escalates to the agent (the safe, capable path); `plan_meals` always uses the
    agent; `out_of_scope` refuses; all other confident labels use the deterministic workflow. The threshold
    comes from config so it can be tuned without code changes.
    """
    settings = get_settings()
    prediction = classifier.predict(message)

    # Zero-signal FIRST: nothing for the agent to act on → cheap clarification, never an agent invocation.
    if not prediction.has_signal:
        return IntentRoute(LOW_SIGNAL_INTENT, prediction.confidence, "clarify")

    # Below the confidence floor (but with real signal) → escalate to the agent rather than risk a wrong
    # deterministic handler — EXCEPT for find_recipe. Semantic search IS the right home for any recipe
    # query at any confidence, so a terse-but-clear ask like "pizza" or "italian pizza" (which classifies
    # as find_recipe but at low confidence, ~0.3) stays on the cheap, reliable workflow instead of paying
    # the agent's slower, tool-call-flaky path for no benefit. The confidence threshold can't separate
    # "correct but terse" from "genuinely ambiguous" for these, so we route by intent, not by number.
    if (
        prediction.confidence < settings.router_confidence_threshold
        and prediction.intent != "find_recipe"
    ):
        return IntentRoute(prediction.intent, prediction.confidence, "agent")

    if prediction.intent == "plan_meals":
        chosen: Route = "agent"
    elif prediction.intent == "out_of_scope":
        chosen = "refuse"
    else:
        chosen = "workflow"
    return IntentRoute(prediction.intent, prediction.confidence, chosen)
