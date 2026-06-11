"""Meal plan — assemble a varied, constraint-safe multi-day plan + ONE shopping list (US3 / FR-014..021).

The bounded agent does the open-ended *retrieval* (it issues `search_recipes` calls across cuisines via
`agent.loop`), but the safety-critical *assembly* is deterministic here — the wall, the ≥3-distinct-cuisine
variety rule, the shortfall accounting, and the single consolidated shopping list are all plain Python, not
trusted to the LLM (Principle II). The flow:

  1. Run the bounded agent loop to gather a pool of wall-cleared candidate recipes (its `surfaced` rows).
  2. If the agent under-delivered (cut off, or it just chatted), deterministically top up the pool with a
     direct retrieval so a plan still gets built — the agent makes it smart, the fallback makes it reliable.
  3. Greedily pick up to N days maximizing distinct KNOWN cuisines (a null/"unknown" cuisine never counts
     toward variety); note any shortfall in length or variety.
  4. Build exactly one deduplicated, serving-scaled shopping list over the chosen recipes.

Every recipe in the plan is wall-cleared (cards come through `recipe_view`); a null cuisine is honestly
excluded from the variety count rather than padding it.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.agent import loop as agent_loop
from app.models.recipe import Recipe
from app.repo import profiles as repo_profiles
from app.schemas.chat import MealPlan, MealPlanDay, ShoppingList
from app.services.shared import recipe_view
from app.services.user import constraint_guard, freshness, rag, shopping_list
from app.services.user.constraint_guard import ConstraintProfile

# Default plan length and the variety target the spec asks for (≥3 distinct cuisines when the corpus allows).
_DEFAULT_DAYS = 3
_MIN_DISTINCT_CUISINES = 3

# Servings an unknown cook (no stored profile) is assumed to cook for — mirrors the rest of the app.
_DEFAULT_SERVINGS = 2

# Cuisine values that do NOT count toward variety (the data-model's "unknown" bucket). A recipe with one of
# these is still usable as a day, it just never contributes a distinct cuisine.
_UNKNOWN_CUISINES = frozenset({"", "unknown", "n/a", "none", "other"})


@dataclass(frozen=True)
class MealPlanResult:
    """A built plan ready for the chat response: the structured plan, its one shopping list, and a reply."""

    plan: MealPlan
    shopping_list: ShoppingList
    reply: str


def _known_cuisine(recipe: Recipe) -> str | None:
    """Return the recipe's cuisine normalized for variety counting, or None when it is unknown.

    Folds case/whitespace and maps the data-model's "unknown" sentinels (and a null cuisine) to None, so a
    recipe whose cuisine we don't really know can never be counted as a distinct cuisine (FR-016).
    """
    raw = (recipe.cuisine or "").strip().lower()
    return None if raw in _UNKNOWN_CUISINES else raw


def _select_for_variety(candidates: list[Recipe], days: int) -> list[Recipe]:
    """Greedily pick up to `days` recipes, first maximizing distinct known cuisines, then filling slots.

    Pass one takes the first recipe of each not-yet-used known cuisine (so the plan spreads across cuisines
    rather than clustering); pass two fills any remaining days from whatever is left, in order, including
    unknown-cuisine recipes. Selection is by recipe id so a candidate is never placed on two days. Input
    order is preserved for a deterministic, testable result.
    """
    selected: list[Recipe] = []
    used_cuisines: set[str] = set()
    chosen_ids: set[object] = set()

    # Pass 1: spread across distinct known cuisines.
    for recipe in candidates:
        if len(selected) >= days:
            break
        cuisine = _known_cuisine(recipe)
        if cuisine is not None and cuisine not in used_cuisines and recipe.id not in chosen_ids:
            selected.append(recipe)
            used_cuisines.add(cuisine)
            chosen_ids.add(recipe.id)

    # Pass 2: fill any leftover days with the remaining candidates (any cuisine), order preserved.
    for recipe in candidates:
        if len(selected) >= days:
            break
        if recipe.id not in chosen_ids:
            selected.append(recipe)
            chosen_ids.add(recipe.id)

    return selected


def _distinct_known_cuisines(recipes: list[Recipe]) -> int:
    """Count the distinct KNOWN cuisines across the chosen recipes (unknown/null never counts — FR-016)."""
    return len({c for c in (_known_cuisine(r) for r in recipes) if c is not None})


def _shortfall_note(selected: list[Recipe], distinct: int, days: int) -> str | None:
    """Return an honest note when the plan fell short of the requested length or ≥3-cuisine variety.

    Per FR-017 the plan must own up to scarcity rather than pad: too few days, or fewer than three
    distinct cuisines when at least three were requestable. Returns None when both targets were met.
    """
    problems: list[str] = []
    if len(selected) < days:
        problems.append(f"only found {len(selected)} of {days} requested days")
    if len(selected) >= _MIN_DISTINCT_CUISINES and distinct < _MIN_DISTINCT_CUISINES:
        problems.append(f"only {distinct} distinct cuisine(s) were available, not {_MIN_DISTINCT_CUISINES}")
    if not problems:
        return None
    return "Heads up: " + "; ".join(problems) + "."


def _resolve_servings(session: Session, profile_id: str) -> int:
    """Read the cook's serving size from their stored profile, defaulting when they have no row yet."""
    profile = repo_profiles.get(session, profile_id)
    return profile.default_servings if profile is not None else _DEFAULT_SERVINGS


def _gather_candidates(
    session: Session,
    message: str,
    cp: ConstraintProfile,
    profile_id: str,
    servings: int,
    days: int,
) -> list[Recipe]:
    """Collect wall-cleared candidate recipes for the plan: the agent's picks, topped up deterministically.

    Runs the bounded agent loop (which searches across cuisines through its tools) and takes the recipe
    rows it surfaced. If that pool is thinner than the requested days — the model was cut off by a bound or
    didn't search enough — it tops up with a direct `rag.fresh_cards` retrieval so a usable plan is still
    produced. De-dupes by id while preserving discovery order (agent picks first).
    """
    outcome = agent_loop.run(session, message, cp, profile_id, servings)
    candidates: list[Recipe] = list(outcome.ctx.surfaced.values())

    if len(candidates) < days:
        # Deterministic top-up: pull a fresh wall-cleared pool for the cook's request and merge it in.
        extra, _cards = rag.fresh_cards(session, message, cp, profile_id, k=max(days, 3))
        seen_ids = {r.id for r in candidates}
        for recipe in extra:
            if recipe.id not in seen_ids:
                candidates.append(recipe)
                seen_ids.add(recipe.id)

    return candidates


def build(
    session: Session,
    message: str,
    cp: ConstraintProfile,
    profile_id: str,
    *,
    days: int = _DEFAULT_DAYS,
) -> MealPlanResult:
    """Build an N-day, constraint-safe, variety-maximized meal plan with exactly one shopping list.

    Resolves the cook's servings, gathers wall-cleared candidates (agent + deterministic top-up), selects
    days to maximize distinct known cuisines, records the chosen recipes to seen-history (freshness), builds
    the single consolidated/scaled shopping list, and composes a grounded reply. The plan's cards come
    through `recipe_view` so the wall holds; an empty corpus yields an empty plan with an honest reply
    rather than an invented one.
    """
    servings = _resolve_servings(session, profile_id)
    candidates = _gather_candidates(session, message, cp, profile_id, servings, days)
    # Belt-and-suspenders wall pass: candidates already come from wall-cleared paths, but re-filtering here
    # guarantees the plan's cards, cuisine count, AND shopping list all operate on the SAME safe set — so a
    # violator can never reach the shopping list even if an upstream path regressed (golden rule #1).
    safe_candidates = constraint_guard.filter(candidates, cp)
    selected = _select_for_variety(safe_candidates, days)

    # Cards via the wall choke point; order matches `selected` so day numbers line up with the chosen rows.
    cards = recipe_view.to_cards(selected, cp)
    plan_days = [MealPlanDay(day=i + 1, recipe=card) for i, card in enumerate(cards)]
    distinct = _distinct_known_cuisines(selected)
    plan = MealPlan(
        days=plan_days,
        distinct_cuisines=distinct,
        shortfall_note=_shortfall_note(selected, distinct, days),
    )

    # Record the chosen recipes so a later plan/search stays fresh (favorites exempt; de-duped in freshness).
    freshness.record_seen(session, profile_id, [r.id for r in selected])

    shopping = shopping_list.build(selected, servings)
    return MealPlanResult(plan=plan, shopping_list=shopping, reply=_compose_reply(plan))


def _compose_reply(plan: MealPlan) -> str:
    """Compose a short grounded reply describing the built plan (titles + cuisine count + any shortfall).

    Drawn only from the assembled plan (real wall-cleared cards), so it never invents a dish. An empty plan
    yields an honest "couldn't build one" message; otherwise it names the days and appends any shortfall.
    """
    if not plan.days:
        return (
            "I couldn't put together a meal plan from the recipes that fit your preferences. "
            "Try widening the request or relaxing a filter."
        )
    titles = ", ".join(day.recipe.title for day in plan.days)
    reply = (
        f"Here's a {len(plan.days)}-day plan across {plan.distinct_cuisines} cuisine(s): {titles}. "
        "I've also built a single shopping list covering all of it."
    )
    if plan.shortfall_note:
        reply = f"{reply} {plan.shortfall_note}"
    return reply
