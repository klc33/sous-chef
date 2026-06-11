"""Output rail — sanitize the reply and re-assert the wall BEFORE the response leaves (and before tracing).

`screen(response, cp, session)` is the last gate every turn passes through. It does three things, in order,
as defense in depth (research §11, FR-007/FR-008/FR-032):

  1. **Leak check + redaction** — runs `core/redaction` over the free-text `reply` (the only field that can
     carry a leaked secret/PII value; recipe cards are built from stored fields) so nothing sensitive can
     leave the process or land in a Phoenix span.
  2. **Wall re-assertion** — re-checks every recipe carried in the response against the cook's
     `ConstraintProfile` and DROPS any violator. Cards are display DTOs without allergen fields, so the
     underlying row is re-fetched by id (the repo is the only DB layer) and the deterministic
     `constraint_guard` runs again. Fail-closed: a card whose id can't be parsed or resolved is dropped.
  3. Returns the sanitized response plus the rail's `GuardrailDecision`.

The cards here already passed the `recipe_view` wall upstream, so a drop should never happen on the normal
path — this layer exists so a bug or a new code path can never leak a violating recipe to a cook.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core import redaction
from app.guardrails.decision import GuardrailDecision
from app.repo import recipes as repo_recipes
from app.schemas.chat import ChatResponse, MealPlan
from app.schemas.recipe import RecipeCard
from app.services.user import constraint_guard
from app.services.user.constraint_guard import ConstraintProfile


def _card_clears_wall(card: RecipeCard, cp: ConstraintProfile, session: Session) -> bool:
    """Return True only when the card's underlying recipe row provably does NOT violate the cook (fail-closed).

    The card lacks allergen/diet fields, so the row is re-fetched by id and run through the deterministic
    `constraint_guard.violates`. An id that can't be parsed to a UUID, or that resolves to no row, cannot be
    verified — so it is treated as a violation and the card is dropped (uncertainty never favors surfacing).
    """
    try:
        recipe_id = uuid.UUID(card.id)
    except (ValueError, TypeError):
        return False
    row = repo_recipes.get_by_id(session, recipe_id)
    if row is None:
        return False
    return not constraint_guard.violates(row, cp)


def screen(
    response: ChatResponse, cp: ConstraintProfile, session: Session
) -> tuple[ChatResponse, GuardrailDecision]:
    """Sanitize the reply, drop any wall-violating recipe, and return the cleaned response + decision.

    Redacts the free-text `reply` (the leak check), then re-asserts the wall over every recipe the response
    carries — the `recipes` cards and each `meal_plan` day's recipe — dropping violators by id-checked
    re-evaluation. The returned `GuardrailDecision` records the output stage ran (`sanitize`) and notes when
    a violator was dropped, so the rare safety drop is visible in traces.
    """
    cleaned_reply = redaction.redact(response.reply)
    safe_recipes = [c for c in response.recipes if _card_clears_wall(c, cp, session)]

    # Re-assert the wall on a meal plan's per-day recipes too (None until US3 builds planning).
    safe_plan: MealPlan | None = response.meal_plan
    if safe_plan is not None:
        safe_days = [d for d in safe_plan.days if _card_clears_wall(d.recipe, cp, session)]
        safe_plan = safe_plan.model_copy(update={"days": safe_days})

    dropped = len(response.recipes) - len(safe_recipes)
    sanitized = response.model_copy(
        update={"reply": cleaned_reply, "recipes": safe_recipes, "meal_plan": safe_plan}
    )
    reason = f"dropped {dropped} wall-violating recipe(s)" if dropped else None
    return sanitized, GuardrailDecision(stage="output", action="sanitize", reason=reason)
