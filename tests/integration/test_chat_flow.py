"""Integration tests for POST /chat — the US1 conversational paths end to end (DB-backed).

These drive the real request → router → workflow → rag → wall → recipe_view → output-rail pipeline
against a real Postgres (pgvector cosine search needs it; SQLite is no substitute). The hosted providers
are mocked in-process: `embed_query` returns a chosen recipe's vector so ranking is deterministic, the
Groq reply is canned, and the trained classifier is bypassed by stubbing `router.route` so a test pins
ONE intent's wiring rather than the model's accuracy (that is gated separately by the classifier eval).

Covered (FR-006/FR-008/FR-009/FR-034):
  * find_recipe → up to 3 ranked, real, wall-cleared cards; a peanut-allergic cook gets zero peanut cards;
  * nutrition_q → the matched recipe's nutrition scaled to the cook's servings (grounded);
  * nutrition_q with no compliant match → an honest "couldn't find that dish".
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.infra import embeddings, llm
from app.models.recipe import Ingredient, NutritionCache, Recipe
from app.services.user import router as router_service
from app.services.user.router import IntentRoute
from sqlalchemy.orm import Session

# Embedding width pinned by migration 0003 — seeded vectors must match the recipes.embedding column.
_DIM = 1536

# Cook owner keys; `auth(<key>)` seeds an active cook with that id and returns its Bearer header.
_PLAIN_COOK = "chat-plain"
_NUT_COOK = "chat-nut"
_FRESH_COOK = "chat-fresh-a"
_FRESH_COOK_B = "chat-fresh-b"
_PLAN_COOK = "chat-plan"


def _one_hot(slot: int) -> list[float]:
    """Return a 1536-d one-hot vector — orthogonal per `slot` so a matching query ranks that recipe first.

    A query embedded to the same slot has cosine distance 0 to its recipe and 1 to every other, making the
    nearest-neighbour ordering deterministic without depending on a real embedding model.
    """
    vec = [0.0] * _DIM
    vec[slot] = 1.0
    return vec


def _seed_recipe(
    session: Session,
    *,
    source_id: str,
    title: str,
    slot: int,
    allergens: list[str],
    calories: int = 400,
) -> uuid.UUID:
    """Insert one complete, embedded dinner recipe (with nutrition) and return its id.

    Diet flags are permissive so only the allergen dimension is under test; the one-hot `slot` fixes its
    position in the vector space; a NutritionCache row (basis 2 servings) makes the nutrition path real.
    """
    recipe = Recipe(
        source="themealdb",
        source_id=source_id,
        title=title,
        category="dinner",
        cuisine="thai",
        servings=2,
        steps=["Mix.", "Serve."],
        allergens=allergens,
        allergen_certain=True,
        is_vegetarian=True,
        is_vegan=True,
        is_pescatarian=True,
        is_complete=True,
        embedding=_one_hot(slot),
        ingredients=[
            Ingredient(position=0, name="tomato", raw_text="tomato", allergen_tags=[]),
            Ingredient(position=1, name="basil", raw_text="basil", allergen_tags=[]),
        ],
    )
    recipe.nutrition = NutritionCache(
        basis_servings=2,
        calories=calories,
        protein_g=20,
        carbs_g=50,
        fat_g=10,
        is_approximate=False,
        unmapped_ingredient_count=0,
    )
    session.add(recipe)
    session.flush()
    return recipe.id


def _stub_providers(monkeypatch: pytest.MonkeyPatch, *, query_slot: int, intent: str) -> None:
    """Mock the hosted embedding + LLM and force the router to a chosen workflow intent.

    `query_slot` is the one-hot the search query embeds to (selecting the nearest recipe); `intent` is the
    label the router emits so the test exercises exactly one workflow handler deterministically.
    """
    monkeypatch.setattr(embeddings, "embed_query", lambda _text: _one_hot(query_slot))
    monkeypatch.setattr(
        llm,
        "chat",
        lambda _messages, **_kwargs: _Resp("Here are some real recipes for you."),
    )
    monkeypatch.setattr(
        router_service, "route", lambda _message: IntentRoute(intent, 0.99, "workflow")
    )


class _Resp:
    """Minimal Groq response stand-in exposing `choices[0].message.content` (what rag reads)."""

    def __init__(self, content: str) -> None:
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


def _tool_call(call_id: str, name: str, arguments: str) -> Any:
    """Build a Groq tool-call stand-in: `.id`, `.function.name`, `.function.arguments` (a JSON string)."""
    function = type("F", (), {"name": name, "arguments": arguments})()
    return type("TC", (), {"id": call_id, "function": function})()


def _tool_resp(tool_calls: list[Any]) -> Any:
    """A Groq response whose assistant message REQUESTS the given tool calls (no content yet)."""
    message = type("M", (), {"content": None, "tool_calls": tool_calls})()
    return type("R", (), {"choices": [type("C", (), {"message": message})()]})()


def _final_resp(content: str) -> Any:
    """A Groq response whose assistant message is the agent's final word (no tool calls)."""
    message = type("M", (), {"content": content, "tool_calls": None})()
    return type("R", (), {"choices": [type("C", (), {"message": message})()]})()


def _seed_dinner(
    session: Session, *, source_id: str, title: str, slot: int, cuisine: str, category: str = "dinner"
) -> uuid.UUID:
    """Insert one complete, embedded, safe recipe with a chosen cuisine + category (for plan/variety tests)."""
    recipe = Recipe(
        source="themealdb",
        source_id=source_id,
        title=title,
        category=category,
        cuisine=cuisine,
        servings=2,
        steps=["Mix.", "Serve."],
        allergens=[],
        allergen_certain=True,
        is_vegetarian=True,
        is_vegan=True,
        is_pescatarian=True,
        is_complete=True,
        embedding=_one_hot(slot),
        ingredients=[Ingredient(position=0, name=f"{title}-veg", quantity=100, unit="g", raw_text="veg")],
    )
    recipe.nutrition = NutritionCache(
        basis_servings=2, calories=400, protein_g=20, carbs_g=50, fat_g=10,
        is_approximate=False, unmapped_ingredient_count=0,
    )
    session.add(recipe)
    session.flush()
    return recipe.id


async def _set_allergy(client, headers: dict[str, str], allergies: list[str]) -> None:
    """Declare the cook's allergies via PUT /profile so the wall has constraints to enforce."""
    resp = await client.put(
        "/profile", headers=headers, json={"diet": "none", "allergies": allergies, "default_servings": 2}
    )
    assert resp.status_code == 200


async def test_find_recipe_returns_ranked_wall_cleared_cards(
    make_user_client, auth, db_session, monkeypatch
) -> None:
    """find_recipe returns <=3 real cards; the peanut recipe is withheld from a nut-allergic cook (FR-008)."""
    safe_id = str(_seed_recipe(db_session, source_id="c-safe", title="Veg Stew", slot=0, allergens=[]))
    nut_id = str(
        _seed_recipe(db_session, source_id="c-nut", title="Peanut Curry", slot=1, allergens=["peanuts"])
    )
    _stub_providers(monkeypatch, query_slot=0, intent="find_recipe")

    async with make_user_client() as client:
        await _set_allergy(client, auth(_NUT_COOK), ["peanuts"])
        resp = await client.post("/chat", headers=auth(_NUT_COOK), json={"message": "something thai for dinner"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "find_recipe"
    ids = {c["id"] for c in body["recipes"]}
    assert len(body["recipes"]) <= 3
    assert nut_id not in ids, "the wall must withhold the peanut recipe from a nut-allergic cook"
    assert safe_id in ids, "the compliant recipe should still surface"
    assert body["reply"] == "Here are some real recipes for you."


async def test_find_recipe_honest_empty_when_no_safe_match(
    make_user_client, auth, db_session, monkeypatch
) -> None:
    """When the only candidate violates the wall, the turn returns no cards and an honest reply (FR-009)."""
    _seed_recipe(db_session, source_id="c-only-nut", title="Peanut Curry", slot=0, allergens=["peanuts"])
    _stub_providers(monkeypatch, query_slot=0, intent="find_recipe")

    async with make_user_client() as client:
        await _set_allergy(client, auth(_NUT_COOK), ["peanuts"])
        resp = await client.post("/chat", headers=auth(_NUT_COOK), json={"message": "a nutty curry"})

    body = resp.json()
    assert body["recipes"] == []
    assert "couldn't find" in body["reply"].lower()


async def test_nutrition_q_returns_scaled_grounded_nutrition(
    make_user_client, auth, db_session, monkeypatch
) -> None:
    """nutrition_q resolves the dish to a real recipe and reports its scaled nutrition (FR-034)."""
    _seed_recipe(db_session, source_id="c-veg", title="Veg Stew", slot=0, allergens=[], calories=400)
    _stub_providers(monkeypatch, query_slot=0, intent="nutrition_q")

    async with make_user_client() as client:
        resp = await client.post(
            "/chat", headers=auth(_PLAIN_COOK), json={"message": "how many calories in veg stew?"}
        )

    body = resp.json()
    assert body["intent"] == "nutrition_q"
    assert body["recipes"] == []
    assert "Veg Stew" in body["reply"]
    assert "400 kcal" in body["reply"]  # basis 2 servings → cook default 2 servings → unscaled


async def test_nutrition_q_honest_when_dish_not_found(
    make_user_client, auth, db_session, monkeypatch
) -> None:
    """nutrition_q gives an honest 'couldn't find that dish' when the wall withholds every candidate."""
    _seed_recipe(db_session, source_id="c-nut2", title="Peanut Curry", slot=0, allergens=["peanuts"])
    _stub_providers(monkeypatch, query_slot=0, intent="nutrition_q")

    async with make_user_client() as client:
        await _set_allergy(client, auth(_NUT_COOK), ["peanuts"])
        resp = await client.post(
            "/chat", headers=auth(_NUT_COOK), json={"message": "calories in peanut curry?"}
        )

    body = resp.json()
    assert "couldn't find" in body["reply"].lower()
    assert body["recipes"] == []


async def test_repeat_query_returns_fresh_recipes(make_user_client, auth, db_session, monkeypatch) -> None:
    """Repeating the same request returns different recipes — the cook's seen-history excludes the first set (US2/SC-001).

    Six equally-relevant safe recipes are seeded (the query embeds to a slot none occupy, so all tie and
    any 3 may surface). The first turn surfaces and records 3; the second excludes those 3 and surfaces 3
    of the remaining — zero overlap, with no exhaustion reset since 3 fresh compliant rows still remain.
    """
    for i in range(6):
        _seed_recipe(db_session, source_id=f"fresh-{i}", title=f"Stew {i}", slot=10 + i, allergens=[])
    _stub_providers(monkeypatch, query_slot=999, intent="find_recipe")

    async with make_user_client() as client:
        await _set_allergy(client, auth(_FRESH_COOK), [])  # create the profile (seen_history FKs to it)
        first = await client.post("/chat", headers=auth(_FRESH_COOK), json={"message": "a stew"})
        second = await client.post("/chat", headers=auth(_FRESH_COOK), json={"message": "a stew"})

    first_ids = {c["id"] for c in first.json()["recipes"]}
    second_ids = {c["id"] for c in second.json()["recipes"]}
    assert len(first_ids) == 3
    assert len(second_ids) == 3
    assert first_ids.isdisjoint(second_ids), "a repeat request must return recipes the cook hasn't seen"


async def test_seen_history_is_per_cook(make_user_client, auth, db_session, monkeypatch) -> None:
    """One cook's seen-history never excludes another cook's results — freshness is profile-scoped (US2)."""
    for i in range(6):
        _seed_recipe(db_session, source_id=f"iso-{i}", title=f"Soup {i}", slot=20 + i, allergens=[])
    _stub_providers(monkeypatch, query_slot=999, intent="find_recipe")

    async with make_user_client() as client:
        await _set_allergy(client, auth(_FRESH_COOK), [])  # both cooks need a profile row (seen_history FK)
        await _set_allergy(client, auth(_FRESH_COOK_B), [])
        # Cook A consumes a page, building up history.
        await client.post("/chat", headers=auth(_FRESH_COOK), json={"message": "a soup"})
        # Cook B asks the same thing for the first time and must still get a full, unaffected page.
        cook_b = await client.post("/chat", headers=auth(_FRESH_COOK_B), json={"message": "a soup"})

    assert len(cook_b.json()["recipes"]) == 3, "a second cook is unaffected by the first cook's history"


async def test_plan_meals_returns_breakfast_lunch_dinner_and_one_shopping_list(
    make_user_client, auth, db_session, monkeypatch
) -> None:
    """A plan_meals turn → a plan with breakfast/lunch/dinner per day and one scaled, deduped list (US3).

    Three safe recipes are seeded in EACH meal category (breakfast/lunch/dinner) across distinct cuisines.
    The router is pinned to the plan_meals route; planning is deterministic per-category retrieval (no agent
    LLM), so only the embedding is mocked. The turn must return a 3-day plan where every day carries one
    recipe of each meal category, plus exactly one consolidated shopping list.
    """
    meals = {
        "breakfast": ["american", "chinese", "turkish"],
        "lunch": ["italian", "vietnamese", "greek"],
        "dinner": ["mexican", "indian", "japanese"],
    }
    slot = 30
    for category, cuisines in meals.items():
        for cuisine in cuisines:
            _seed_dinner(
                db_session,
                source_id=f"plan-{category}-{cuisine}",
                title=f"{cuisine.title()} {category.title()}",
                slot=slot,
                cuisine=cuisine,
                category=category,
            )
            slot += 1

    # The query embeds to a slot no recipe occupies, so within each category all tie (any may surface).
    monkeypatch.setattr(embeddings, "embed_query", lambda _text: _one_hot(999))
    monkeypatch.setattr(
        router_service, "route", lambda _message: IntentRoute("plan_meals", 0.99, "agent")
    )

    async with make_user_client() as client:
        await _set_allergy(client, auth(_PLAN_COOK), [])  # create the profile (seen_history FKs to it)
        resp = await client.post("/chat", headers=auth(_PLAN_COOK), json={"message": "plan 3 days of meals"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "plan_meals"
    plan = body["meal_plan"]
    assert plan is not None
    assert len(plan["days"]) == 3, "a 3-day request yields three days"
    # Every day carries one real, wall-cleared recipe of each meal category (never invented).
    for day in plan["days"]:
        assert day["breakfast"]["category"] == "breakfast"
        assert day["lunch"]["category"] == "lunch"
        assert day["dinner"]["category"] == "dinner"
    assert plan["shortfall_note"] is None, "the corpus covers all three meals for three days"
    shopping = body["shopping_list"]
    assert shopping is not None, "a plan carries exactly one shopping list"
    assert len(shopping["lines"]) >= 1
