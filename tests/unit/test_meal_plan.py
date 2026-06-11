"""Unit tests for the meal-plan assembler (services/user/meal_plan.py) — US3, no DB/LLM.

These pin the deterministic assembly guarantees (FR-014..017) with the agent loop + freshness stubbed so
the test exercises meal_plan's OWN logic — variety selection, the unknown-cuisine rule, shortfall notes,
and the wall — not the providers it sits on:
  * the plan maximizes distinct KNOWN cuisines (>=3 when the candidate pool allows);
  * a null/"unknown" cuisine never counts toward the variety total;
  * a too-thin pool yields a shortfall note rather than padding/invention;
  * a violating candidate never reaches the plan or its shopping list (the wall holds on this path).

The agent loop is replaced by a stub that "surfaces" a fixed candidate pool, so no Groq/DB call happens.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from app.agent.tools import ToolContext
from app.services.user import meal_plan
from app.services.user.constraint_guard import ConstraintProfile


def _recipe(
    rid: str,
    title: str,
    cuisine: str | None,
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
        category="dinner",
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
    """Stub the agent loop (candidate source), freshness, and servings so build() runs without DB/LLM.

    `pool` is the candidate list the stubbed agent "surfaced"; the test sets it. The agent loop returns a
    LoopOutcome whose ToolContext.surfaced holds that pool; record_seen is a no-op; servings resolves to 2.
    """
    state: dict[str, Any] = {"pool": []}

    def _fake_run(_session: Any, _msg: str, cp: ConstraintProfile, profile_id: str, servings: int) -> Any:
        ctx = ToolContext(session=None, cp=cp, profile_id=profile_id, servings=servings)
        ctx.surfaced = {r.id: r for r in state["pool"]}
        return SimpleNamespace(text="", ctx=ctx)

    monkeypatch.setattr(meal_plan.agent_loop, "run", _fake_run)
    # The pool already covers the requested days, so the deterministic rag top-up is never triggered;
    # stub it anyway so an accidental call can't hit the DB.
    monkeypatch.setattr(meal_plan.rag, "fresh_cards", lambda *_a, **_k: ([], []))
    monkeypatch.setattr(meal_plan.freshness, "record_seen", lambda *_a, **_k: None)
    monkeypatch.setattr(meal_plan, "_resolve_servings", lambda _session, _pid: 2)
    return state


def test_plan_maximizes_distinct_cuisines(stub_pipeline: dict[str, Any]) -> None:
    """A pool spanning >=3 cuisines yields a 3-day plan with 3 distinct cuisines and no shortfall (FR-016)."""
    stub_pipeline["pool"] = [
        _recipe("1", "Pad Thai", "thai"),
        _recipe("2", "Carbonara", "italian"),
        _recipe("3", "Tacos", "mexican"),
        _recipe("4", "Green Curry", "thai"),  # extra thai — should not be chosen over a new cuisine
    ]
    result = meal_plan.build(None, "plan 3 dinners", ConstraintProfile.default(), "cook-1")

    assert len(result.plan.days) == 3
    assert result.plan.distinct_cuisines == 3
    assert result.plan.shortfall_note is None
    titles = {d.recipe.title for d in result.plan.days}
    assert titles == {"Pad Thai", "Carbonara", "Tacos"}  # one per distinct cuisine, the duplicate skipped


def test_unknown_cuisine_not_counted(stub_pipeline: dict[str, Any]) -> None:
    """A null/'unknown' cuisine fills a day but never counts toward distinct cuisines (FR-016/017)."""
    stub_pipeline["pool"] = [
        _recipe("1", "Pad Thai", "thai"),
        _recipe("2", "Mystery Bowl", None),
        _recipe("3", "Unknown Stew", "unknown"),
    ]
    result = meal_plan.build(None, "plan 3 dinners", ConstraintProfile.default(), "cook-1")

    assert len(result.plan.days) == 3  # all three fill days
    assert result.plan.distinct_cuisines == 1  # only 'thai' is a known cuisine
    assert result.plan.shortfall_note is not None  # <3 distinct cuisines → honest shortfall


def test_shortfall_note_when_too_few_days(stub_pipeline: dict[str, Any]) -> None:
    """A pool smaller than the requested days yields a shortfall note, never a padded/invented plan (FR-017)."""
    stub_pipeline["pool"] = [_recipe("1", "Pad Thai", "thai")]
    result = meal_plan.build(None, "plan 3 dinners", ConstraintProfile.default(), "cook-1")

    assert len(result.plan.days) == 1
    assert result.plan.shortfall_note is not None
    assert "1 of 3" in result.plan.shortfall_note


def test_violating_recipe_never_reaches_plan_or_shopping_list(stub_pipeline: dict[str, Any]) -> None:
    """A peanut recipe in the candidate pool is dropped from the plan AND the shopping list (the wall holds)."""
    stub_pipeline["pool"] = [
        _recipe("safe", "Veg Stew", "italian"),
        _recipe("nut", "Peanut Curry", "thai", allergens=("peanuts",)),
    ]
    cp = ConstraintProfile(allergies=frozenset({"peanuts"}))
    result = meal_plan.build(None, "plan dinners", cp, "cook-1")

    plan_titles = {d.recipe.title for d in result.plan.days}
    assert "Peanut Curry" not in plan_titles
    assert "Veg Stew" in plan_titles
    # The shopping list must not contain the violator's ingredient either.
    list_recipes = {recipe for line in result.shopping_list.lines for recipe in line.from_recipes}
    assert "Peanut Curry" not in list_recipes


def test_single_shopping_list_for_the_plan(stub_pipeline: dict[str, Any]) -> None:
    """Every plan carries exactly one shopping list aggregating its chosen recipes (FR-018)."""
    stub_pipeline["pool"] = [_recipe("1", "Pad Thai", "thai"), _recipe("2", "Carbonara", "italian")]
    result = meal_plan.build(None, "plan dinners", ConstraintProfile.default(), "cook-1")

    assert result.shopping_list is not None
    # Two distinct ingredients (one per recipe), each scaled at factor 1 (servings 2 == basis 2).
    assert len(result.shopping_list.lines) == 2
