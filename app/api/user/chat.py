"""POST /chat — the single conversational turn endpoint (the constitution's turn flow, end to end).

Mirrors contracts/chat.openapi.yaml. One turn flows exactly as mandated:
`input rail → router → workflow | agent → (recipes via recipe_view = the wall) → output rail`. Cook
identity is the passwordless `X-Profile-ID` header (never the body); the cook's `ConstraintProfile` is
resolved once and threaded to every stage. A refused input short-circuits before routing and returns a
safe `ChatResponse(refused=true)`. The endpoint is rate-limited PER PROFILE via slowapi so one cook can't
exhaust the shared hosted-API budget.

This foundational phase wires the whole pipeline; the agent path (plan_meals / low-confidence) returns an
honest interim reply until US3 implements `app/agent/loop.py`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.agent import loop as agent_loop
from app.api.deps import get_db, require_profile_id
from app.guardrails import input_rails, output_rails
from app.repo import profiles as repo_profiles
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.shared import recipe_view
from app.services.user import meal_plan as meal_plan_service
from app.services.user import router as router_service
from app.services.user import workflow
from app.services.user.constraint_guard import ConstraintProfile

# Servings an unknown cook (no stored profile) is assumed to cook for — mirrors the rest of the app.
_DEFAULT_SERVINGS = 2

# Annotated dependency aliases (matches the recipes router idiom).
ProfileId = Annotated[str, Depends(require_profile_id)]
DbSession = Annotated[Session, Depends(get_db)]

# Per-profile rate limit: the limit key is the cook's profile-ID (falling back to client address when the
# header is somehow absent), so each cook gets an independent budget against the shared hosted APIs.
_DEFAULT_RATE = "30/minute"


def _profile_rate_key(request: Request) -> str:
    """Return the rate-limit bucket key for a request: the X-Profile-ID header, else the client address."""
    return request.headers.get("X-Profile-ID") or get_remote_address(request)


limiter = Limiter(key_func=_profile_rate_key)

router = APIRouter()


def _resolve_profile(session: Session, profile_id: str) -> ConstraintProfile:
    """Resolve the cook's ConstraintProfile from the stored row, or the permissive default for a new cook."""
    profile = repo_profiles.get(session, profile_id)
    return ConstraintProfile.from_row(profile) if profile is not None else ConstraintProfile.default()


def _agent(
    session: Session,
    intent: str,
    message: str,
    cp: ConstraintProfile,
    profile_id: str,
) -> ChatResponse:
    """Serve an agent-routed turn via the bounded agent (US3): a meal plan, or a general tool-assisted reply.

    `plan_meals` is the structured path — `meal_plan.build` runs the bounded loop to gather candidates and
    deterministically assembles the varied plan + its single shopping list (FR-014..021). Any OTHER
    agent-routed turn (a low-confidence/ambiguous escalation) runs the loop generically and returns its
    grounded text plus any wall-cleared cards the tools surfaced — never an invented answer. Recipes still
    only ever leave through `recipe_view`, so the wall holds on both agent paths.
    """
    if intent == "plan_meals":
        result = meal_plan_service.build(session, message, cp, profile_id)
        return ChatResponse(
            reply=result.reply,
            intent="plan_meals",
            meal_plan=result.plan,
            shopping_list=result.shopping_list,
        )

    # Ambiguous escalation: let the bounded agent help with its tools, then surface what it found.
    profile = repo_profiles.get(session, profile_id)
    servings = profile.default_servings if profile is not None else _DEFAULT_SERVINGS
    outcome = agent_loop.run(session, message, cp, profile_id, servings)
    cards = recipe_view.to_cards(list(outcome.ctx.surfaced.values()), cp)
    if outcome.text:
        reply = outcome.text  # the agent spoke — use its grounded closing message
    elif cards:
        reply = "Here are some ideas based on what I found."
    else:
        reply = "I'm not sure what you're after — could you tell me what you'd like to cook?"
    return ChatResponse(reply=reply, intent=intent, recipes=cards)


@router.post("/chat", response_model=ChatResponse)
@limiter.limit(_DEFAULT_RATE)
def chat_turn(
    request: Request,
    body: ChatRequest,
    profile_id: ProfileId,
    session: DbSession,
) -> ChatResponse:
    """Handle one conversational turn and return the grounded ChatResponse.

    Resolves the cook's constraints, screens the message through the input rail (a refusal short-circuits
    to a safe reply before any routing), routes the turn via the trained classifier, dispatches to the
    workflow (or the interim agent path), and finally runs the output rail (redaction + wall re-assert)
    before the response leaves. `request` is required by the slowapi limiter; the limit is per profile-ID.
    """
    cp = _resolve_profile(session, profile_id)

    # Input rail FIRST — refuse injection/jailbreak/override before the message reaches routing (US5). A
    # refusal short-circuits to a safe ChatResponse(refused=true); when the rail neutralized an embedded
    # injection but a safe remainder survives, it hands back a cleaned message we route instead (FR-033).
    decision = input_rails.screen(body.message)
    if decision.action == "refuse":
        refusal = ChatResponse(
            reply=decision.reason or "I can't help with that request.",
            intent="refused",
            refused=True,
        )
        sanitized, _ = output_rails.screen(refusal, cp, session)
        return sanitized

    message = decision.sanitized_message or body.message

    intent_route = router_service.route(message)
    if intent_route.route == "agent":
        response = _agent(session, intent_route.intent, message, cp, profile_id)
    else:
        # workflow | refuse (out_of_scope) | clarify (low_signal) all dispatch by intent on the cheap
        # deterministic path — only genuine, signal-bearing turns reach the agent above. The optional
        # explicit category hint from the body is passed through as a retrieval pre-filter.
        response = workflow.handle(
            session, intent_route.intent, message, cp, profile_id, body.category
        )

    # Output rail LAST — redact + re-assert the wall before the reply (and any trace span) leaves.
    sanitized, _ = output_rails.screen(response, cp, session)
    return sanitized
