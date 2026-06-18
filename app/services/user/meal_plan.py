"""Meal plan — assemble a varied, constraint-safe multi-day plan + ONE shopping list (US3 / FR-014..021).

Each day of the plan is a full set of meals — **breakfast, lunch, and dinner** — and the whole plan is
covered by exactly one consolidated shopping list. Because the corpus tags every recipe with one fixed
category at ingestion (`breakfast | lunch | dinner | hot_drink | cold_drink`), the assembly is a
deterministic per-category retrieval, not an LLM guess (Principle II):

  1. For each meal category, run a freshness-aware, wall-cleared semantic retrieval for the cook's request,
     asking for up to N distinct recipes (N = requested days).
  2. Pair them up by day: day i gets the i-th breakfast, lunch, and dinner. A category that returns fewer
     than N recipes leaves that meal empty on the later days and is owned up to in the shortfall note —
     never padded or invented.
  3. Build exactly one deduplicated, serving-scaled shopping list over every chosen recipe across all days.

Every card comes through `recipe_view` (the wall choke point) and each retrieval re-applies the
deterministic allergen wall, so a violating recipe can never reach a meal slot or the shopping list
(golden rule #1). A null/"unknown" cuisine is honestly excluded from the variety count rather than padding it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.recipe import Category, Recipe
from app.repo import profiles as repo_profiles
from app.schemas.chat import MealPlan, MealPlanDay, ShoppingList
from app.schemas.recipe import RecipeCard
from app.services.shared import recipe_view
from app.services.user import constraint_guard, rag, shopping_list
from app.services.user.constraint_guard import ConstraintProfile

# The three meal slots that make up each day of a plan, in the order they're shown.
_MEAL_SLOTS: tuple[Category, ...] = (Category.BREAKFAST, Category.LUNCH, Category.DINNER)
_SLOT_LABELS = {Category.BREAKFAST: "breakfast", Category.LUNCH: "lunch", Category.DINNER: "dinner"}

# Default plan length when the cook names no number, and the hard cap on a single request.
_DEFAULT_DAYS = 3
_MAX_DAYS = 7

# Servings an unknown cook (no stored profile) is assumed to cook for — mirrors the rest of the app.
_DEFAULT_SERVINGS = 2

# Cuisine values that do NOT count toward variety (the data-model's "unknown" bucket).
_UNKNOWN_CUISINES = frozenset({"", "unknown", "n/a", "none", "other"})

# Number words a cook might use for the plan length ("plan five dinners").
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "couple": 2, "few": 3,
}
# Pull a requested count out of "<n> days/dinners/meals/nights" (digit or number word).
_DAYS_REQUEST = re.compile(
    r"\b(\d+|one|two|three|four|five|six|seven|couple|few)\b\s*-?\s*(?:of\s+)?"
    r"(?:day|days|dinner|dinners|meal|meals|night|nights)\b",
    re.IGNORECASE,
)


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


def _distinct_known_cuisines(recipes: list[Recipe]) -> int:
    """Count the distinct KNOWN cuisines across the chosen recipes (unknown/null never counts — FR-016)."""
    return len({c for c in (_known_cuisine(r) for r in recipes) if c is not None})


def _parse_requested_days(message: str) -> int:
    """Pull the requested plan length from the cook's message, clamped to [1, _MAX_DAYS]; default when absent.

    Reads a count expressed as a digit or a number word followed by a day/meal noun ("five dinners",
    "3 days"). A missing or unparseable count falls back to `_DEFAULT_DAYS`, and any value is clamped so a
    cook can't request an unboundedly long plan.
    """
    match = _DAYS_REQUEST.search(message)
    if match is None:
        return _DEFAULT_DAYS
    token = match.group(1).lower()
    value = int(token) if token.isdigit() else _NUMBER_WORDS.get(token, _DEFAULT_DAYS)
    return max(1, min(value, _MAX_DAYS))


def _resolve_servings(session: Session, profile_id: str) -> int:
    """Read the cook's serving size from their stored profile, defaulting when they have no row yet."""
    profile = repo_profiles.get(session, profile_id)
    return profile.default_servings if profile is not None else _DEFAULT_SERVINGS


def _retrieve_slot(
    session: Session,
    message: str,
    cp: ConstraintProfile,
    profile_id: str,
    category: Category,
    days: int,
) -> list[Recipe]:
    """Retrieve up to `days` fresh, wall-cleared recipes for one meal category, ordered by relevance.

    Delegates to the shared freshness-aware retrieval (`rag.fresh_cards`, which excludes seen-history and
    resets on exhaustion) filtered to this single category, then re-applies the allergen wall as a
    belt-and-suspenders pass so the meal slot, the variety count, and the shopping list all operate on the
    SAME safe set (golden rule #1). Returns the recipe rows; cook-facing cards are built later via the wall.
    """
    rows, _cards = rag.fresh_cards(session, message, cp, profile_id, category=category, k=days)
    return constraint_guard.filter(rows, cp)


def _shortfall_note(
    per_slot: dict[Category, list[Recipe]], actual_days: int, requested_days: int
) -> str | None:
    """Return an honest note when the plan fell short of the requested length or a meal slot couldn't fill.

    Per FR-017 the plan owns up to scarcity rather than padding: fewer days than asked, or a breakfast/
    lunch/dinner the corpus couldn't supply for every day. Returns None when every slot covered every day.
    """
    problems: list[str] = []
    if actual_days < requested_days:
        problems.append(f"only found enough recipes for {actual_days} of {requested_days} requested days")
    short_meals = [
        _SLOT_LABELS[cat] for cat in _MEAL_SLOTS if len(per_slot.get(cat, [])) < actual_days
    ]
    if short_meals:
        problems.append("couldn't fill every day's " + ", ".join(short_meals))
    if not problems:
        return None
    return "Heads up: " + "; ".join(problems) + "."


def build(
    session: Session,
    message: str,
    cp: ConstraintProfile,
    profile_id: str,
    *,
    days: int | None = None,
) -> MealPlanResult:
    """Build an N-day, constraint-safe plan with a breakfast/lunch/dinner per day and one shopping list.

    Resolves the cook's servings and the requested length, retrieves a fresh wall-cleared set per meal
    category, pairs them by day (day i = the i-th breakfast/lunch/dinner), caps the length to what the
    best-covered meal can actually fill (so no fully-empty day is shown), builds the single consolidated/
    scaled shopping list over every chosen recipe, and composes a grounded reply. Cards come through
    `recipe_view`, so the wall holds; an empty corpus yields an empty plan with an honest reply.
    """
    servings = _resolve_servings(session, profile_id)
    requested_days = days if days is not None else _parse_requested_days(message)

    # Retrieve a fresh, wall-cleared candidate list for each meal slot independently.
    per_slot: dict[Category, list[Recipe]] = {
        cat: _retrieve_slot(session, message, cp, profile_id, cat, requested_days) for cat in _MEAL_SLOTS
    }

    # Show as many days as the best-covered meal can fill (capped to the request) — never an all-empty day.
    actual_days = min(requested_days, max((len(rows) for rows in per_slot.values()), default=0))

    # The recipes actually placed on a day (used for the shopping list + variety count).
    chosen: list[Recipe] = [row for cat in _MEAL_SLOTS for row in per_slot[cat][:actual_days]]
    # Build cards once through the wall choke point, keyed by id so slots map back regardless of filtering.
    cards_by_id: dict[str, RecipeCard] = {c.id: c for c in recipe_view.to_cards(chosen, cp)}

    def _card(row: Recipe | None) -> RecipeCard | None:
        """Look up the wall-cleared card for a chosen recipe row (None for an unfilled slot)."""
        return cards_by_id.get(str(row.id)) if row is not None else None

    def _slot(cat: Category, i: int) -> Recipe | None:
        """Return the i-th retrieved recipe for a meal slot, or None when that slot ran out for this day."""
        rows = per_slot[cat]
        return rows[i] if i < len(rows) else None

    plan_days = [
        MealPlanDay(
            day=i + 1,
            breakfast=_card(_slot(Category.BREAKFAST, i)),
            lunch=_card(_slot(Category.LUNCH, i)),
            dinner=_card(_slot(Category.DINNER, i)),
        )
        for i in range(actual_days)
    ]

    plan = MealPlan(
        days=plan_days,
        distinct_cuisines=_distinct_known_cuisines(chosen),
        shortfall_note=_shortfall_note(per_slot, actual_days, requested_days),
    )

    shopping = shopping_list.build(chosen, servings)
    return MealPlanResult(plan=plan, shopping_list=shopping, reply=_compose_reply(plan))


def _compose_reply(plan: MealPlan) -> str:
    """Compose a short grounded reply describing the built plan (day count + cuisine count + any shortfall).

    Drawn only from the assembled plan (real wall-cleared cards), so it never invents a dish. An empty plan
    yields an honest "couldn't build one" message; otherwise it states the coverage and appends any shortfall.
    """
    if not plan.days:
        return (
            "I couldn't put together a meal plan from the recipes that fit your preferences. "
            "Try widening the request or relaxing a filter."
        )
    reply = (
        f"Here's a {len(plan.days)}-day plan with breakfast, lunch, and dinner for each day "
        f"across {plan.distinct_cuisines} cuisine(s). "
        "I've also built a single shopping list covering all of it."
    )
    if plan.shortfall_note:
        reply = f"{reply} {plan.shortfall_note}"
    return reply
