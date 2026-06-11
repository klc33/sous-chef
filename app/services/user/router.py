"""The router — turn a classified intent into a dispatch decision (workflow | agent | refuse).

Every turn flows through here right after the input rail. `route(message)` calls the trained classifier
(`app/classifier/predict.py`) and maps the label + confidence to one of three handlers per
contracts/classifier.md:
  * zero-signal (no known vocabulary matched) → clarify (cheap re-prompt; NEVER the agent — FR-004a)
  * `plan_meals`                          → agent (the multi-step, multi-tool path)
  * confidence < `router_confidence_threshold` WITH real signal → agent (escalate to the safer, capable path)
  * `out_of_scope`                        → refuse (safe canned redirect)
  * everything else                       → workflow (deterministic handler)

The zero-signal check runs FIRST and short-circuits to a cheap clarification: a message that matches no
known vocabulary gives the agent nothing to act on, so spending an expensive agent call on it is waste
(FR-004a). Only genuinely-ambiguous turns — low confidence yet with real matched signal — escalate to the
agent. Either way a misclassification degrades cost/quality, never safety: every path still passes the
wall + output rail (FR-004).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.classifier import predict as classifier
from app.config import get_settings

# "clarify" is a cheap deterministic re-prompt for zero-signal turns; "refuse" is the safe out_of_scope
# redirect. Both stay on the deterministic path — neither reaches the agent.
Route = Literal["workflow", "agent", "refuse", "clarify"]

# Intent label assigned to a zero-signal turn (no real classification happened — see FR-004a).
LOW_SIGNAL_INTENT = "low_signal"


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
    # deterministic handler.
    if prediction.confidence < settings.router_confidence_threshold:
        return IntentRoute(prediction.intent, prediction.confidence, "agent")

    if prediction.intent == "plan_meals":
        chosen: Route = "agent"
    elif prediction.intent == "out_of_scope":
        chosen = "refuse"
    else:
        chosen = "workflow"
    return IntentRoute(prediction.intent, prediction.confidence, chosen)
