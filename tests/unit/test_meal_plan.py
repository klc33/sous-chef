"""Unit tests for the meal-plan assembler (services/user/meal_plan.py) — US3, no DB/LLM.

These pin the deterministic assembly guarantees (FR-014..018) with retrieval + freshness stubbed so the
test exercises meal_plan's OWN logic — per-category pairing into breakfast/lunch/dinner, the requested-
length parsing + cap, shortfall notes, the variety count, and the wall:
  * each day carries a breakfast, lunch, and dinner drawn from the matching category;
  * the requested length is honored (digit or number word) and capped to what the corpus can fill;
  * a too-thin meal category yields a shortfall note rather than padding/invention;
  * a violating candidate never reaches a meal slot or the shopping list (the wall holds on this path).

Retrieval is replaced by a stub that returns a fixed per-category pool, so no Groq/DB call happens.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from app.models.recipe import Category
from app.services.user import meal_plan
from app.services.user.constraint_guard import ConstraintProfile


def _recipe(
    rid: str,
    title: str,
    cuisine: str | None,
    category: str = "dinner",
    *,
    allergens: tuple[str, ...] = (),
    certain: bool = True,
) -> SimpleNamespace:
    """Build a recipe stand-in with the fields meal_plan / wall / cards / shopping-list all read.

    Permissive diet flags isolate the cuisine + allergen dimensions under test; one quantified ingredient
    makes the shopping-list step real; `servings=2` is the scaling basis.
    """
    return SimpleNamespace(
        id=rid,
        title=title,
        cuisine=cuisine,
        category=category,
        image_url=None,
        servings=2,
        ingredients=[SimpleNamespace(name=f"{title}-veg", quantity=100, unit="g")],
        allergens=list(allergens),
        allergen_certain=certain,
        is_vegan=True,
        is_vegetarian=True,
        is_pescatarian=True,
    )


@pytest.fixture
def stub_pipeline(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub per-category retrieval and servings so build() runs without DB/LLM.

    `slots` maps a Category to the recipe list that category's retrieval should "surface"; the test sets it.
    `rag.fresh_cards` is replaced to return the rows for the requested category (capped to k, like the real
    freshness-aware retrieval), and servings resolves to 2.
    """
    state: dict[str, Any] = {"slots": {}}

    def _fake_fresh_cards(
        _session: Any, _msg: str, _cp: ConstraintProfile, _pid: str, *, category: Category, k: int
    ) -> tuple[list[Any], list[Any]]:
        rows = list(state["slots"].get(category, []))[:k]
        return rows, []  # cards are rebuilt by meal_plan via recipe_view; the row list is what it uses

    monkeypatch.setattr(meal_plan.rag, "fresh_cards", _fake_fresh_cards)
    monkeypatch.setattr(meal_plan, "_resolve_servings", lambda _session, _pid: 2)
    return state


def _set_slots(
    state: dict[str, Any],
    breakfast: list[Any] | None = None,
    lunch: list[Any] | None = None,
    dinner: list[Any] | None = None,
) -> None:
    """Helper: load the per-category candidate pools the stubbed retrieval should return."""
    state["slots"] = {
        Category.BREAKFAST: breakfast or [],
        Category.LUNCH: lunch or [],
        Category.DINNER: dinner or [],
    }


def test_each_day_has_breakfast_lunch_dinner(stub_pipeline: dict[str, Any]) -> None:
    """A pool covering all three meals for 2 days yields 2 days, each with all three slots filled."""
    _set_slots(
        stub_pipeline,
        breakfast=[_recipe("b1", "Oatmeal", "american", "breakfast"), _recipe("b2", "Congee", "chinese", "breakfast")],
        lunch=[_recipe("l1", "Caesar Salad", "italian", "lunch"), _recipe("l2", "Pho", "vietnamese", "lunch")],
        dinner=[_recipe("d1", "Tacos", "mexican", "dinner"), _recipe("d2", "Curry", "indian", "dinner")],
    )
    result = meal_plan.build(None, "plan 2 days of meals", ConstraintProfile.default(), "cook-1")

    assert len(result.plan.days) == 2
    for day in result.plan.days:
        assert day.breakfast is not None
        assert day.lunch is not None
        assert day.dinner is not None
    assert result.plan.shortfall_note is None
    # Day 1 takes the first recipe of each category; day 2 the second (distinct, no repeats).
    assert result.plan.days[0].breakfast.title == "Oatmeal"
    assert result.plan.days[1].breakfast.title == "Congee"


def test_requested_length_parsed_and_capped(stub_pipeline: dict[str, Any]) -> None:
    """A number-word request ('five') is honored when the corpus can fill it across all three meals."""
    _set_slots(
        stub_pipeline,
        breakfast=[_recipe(f"b{i}", f"B{i}", "american", "breakfast") for i in range(6)],
        lunch=[_recipe(f"l{i}", f"L{i}", "italian", "lunch") for i in range(6)],
        dinner=[_recipe(f"d{i}", f"D{i}", "mexican", "dinner") for i in range(6)],
    )
    result = meal_plan.build(None, "set up a meal plan for five days", ConstraintProfile.default(), "cook-1")

    assert len(result.plan.days) == 5
    assert result.plan.shortfall_note is None


def test_shortfall_when_a_meal_cannot_fill_every_day(stub_pipeline: dict[str, Any]) -> None:
    """Fewer breakfasts than days fills what it can and notes the gap, never padding/inventing (FR-017)."""
    _set_slots(
        stub_pipeline,
        breakfast=[_recipe("b1", "Oatmeal", "american", "breakfast")],  # only one breakfast
        lunch=[_recipe("l1", "Salad", "italian", "lunch"), _recipe("l2", "Pho", "vietnamese", "lunch")],
        dinner=[_recipe("d1", "Tacos", "mexican", "dinner"), _recipe("d2", "Curry", "indian", "dinner")],
    )
    result = meal_plan.build(None, "plan 2 days", ConstraintProfile.default(), "cook-1")

    assert len(result.plan.days) == 2  # lunch/dinner can cover 2 days
    assert result.plan.days[0].breakfast is not None
    assert result.plan.days[1].breakfast is None  # ran out of breakfasts
    assert result.plan.shortfall_note is not None
    assert "breakfast" in result.plan.shortfall_note


def test_shortfall_when_too_few_days(stub_pipeline: dict[str, Any]) -> None:
    """A request longer than the best-covered meal caps the plan and notes the day shortfall (FR-017)."""
    _set_slots(
        stub_pipeline,
        breakfast=[_recipe("b1", "Oatmeal", "american", "breakfast")],
        lunch=[_recipe("l1", "Salad", "italian", "lunch")],
        dinner=[_recipe("d1", "Tacos", "mexican", "dinner")],
    )
    result = meal_plan.build(None, "plan 3 days", ConstraintProfile.default(), "cook-1")

    assert len(result.plan.days) == 1  # only one of each meal available
    assert result.plan.shortfall_note is not None
    assert "1 of 3" in result.plan.shortfall_note


def test_violating_recipe_never_reaches_plan_or_shopping_list(stub_pipeline: dict[str, Any]) -> None:
    """A peanut dinner is dropped from the plan AND the shopping list (the wall holds on this path)."""
    _set_slots(
        stub_pipeline,
        breakfast=[_recipe("b1", "Oatmeal", "american", "breakfast")],
        lunch=[_recipe("l1", "Salad", "italian", "lunch")],
        dinner=[_recipe("nut", "Peanut Curry", "thai", "dinner", allergens=("peanuts",))],
    )
    cp = ConstraintProfile(allergies=frozenset({"peanuts"}))
    result = meal_plan.build(None, "plan dinners", cp, "cook-1")

    # The peanut dinner is filtered out, so no day carries it and the dinner slot is empty.
    titles = {
        card.title
        for day in result.plan.days
        for card in (day.breakfast, day.lunch, day.dinner)
        if card is not None
    }
    assert "Peanut Curry" not in titles
    list_recipes = {recipe for line in result.shopping_list.lines for recipe in line.from_recipes}
    assert "Peanut Curry" not in list_recipes


def test_single_shopping_list_for_the_plan(stub_pipeline: dict[str, Any]) -> None:
    """Every plan carries exactly one shopping list aggregating all of its chosen recipes (FR-018)."""
    _set_slots(
        stub_pipeline,
        breakfast=[_recipe("b1", "Oatmeal", "american", "breakfast")],
        lunch=[_recipe("l1", "Salad", "italian", "lunch")],
        dinner=[_recipe("d1", "Tacos", "mexican", "dinner")],
    )
    result = meal_plan.build(None, "plan 1 day", ConstraintProfile.default(), "cook-1")

    assert result.shopping_list is not None
    # Three distinct ingredients (one per meal), each scaled at factor 1 (servings 2 == basis 2).
    assert len(result.shopping_list.lines) == 3
