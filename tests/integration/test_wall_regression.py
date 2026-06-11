"""The architectural safety regression (T039): no cook-facing path may surface a violating recipe.

Golden rule #1 says the wall is the grade — and the structural guarantee is that EVERY recipe leaves
through `services/shared/recipe_view`, which runs `constraint_guard` first. This test pins that guarantee
end-to-end: it drives the real HTTP endpoints for a nut-allergic cook and asserts a planted peanut recipe
surfaces on NONE of them. It is parametrized over every cook-facing recipe path (`GET /recipes`,
`GET /recipes/{id}`, `GET /favorites`); if someone adds a path that builds a card/detail without
recipe_view, extending this parametrization to it makes the bypass fail loudly here.

It lives with the integration suite (not the pure unit file `test_constraint_guard.py`, which pins the
predicate in isolation) because exercising the actual endpoints requires the DB-backed app harness —
that is the whole point: the regression must prove the wired paths, not a stand-in, enforce the wall.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from app.infra import embeddings, llm_groq
from app.models.recipe import Ingredient, NutritionCache, Recipe
from app.services.user import router as router_service
from app.services.user.router import IntentRoute
from sqlalchemy.orm import Session

# A cook who will declare a peanut allergy; the wall must withhold the peanut recipe from every path.
_NUT_COOK = {"X-Profile-ID": "regression-nut"}

# The three cook-facing recipe paths the regression sweeps. Adding a new surfacing endpoint? Add it here.
_PATHS = ["GET /recipes", "GET /recipes/{id}", "GET /favorites"]

# Embedding width pinned by migration 0003 — seeded vectors must match the recipes.embedding column.
_DIM = 1536


def _add_recipe(
    session: Session,
    *,
    source_id: str,
    title: str,
    allergens: list[str],
    ingredient_names: list[str],
) -> uuid.UUID:
    """Insert one complete, openable dinner recipe (with nutrition + embedding) and return its id.

    Diet flags are permissive so only the allergen dimension is under test; a NutritionCache row is
    attached so the recipe is reachable on the detail path (which 404s a recipe lacking nutrition); a
    nonzero embedding makes it a candidate on the vector-search (rag) path too.
    """
    recipe = Recipe(
        source="themealdb",
        source_id=source_id,
        title=title,
        category="dinner",
        servings=2,
        steps=["Mix.", "Serve."],
        allergens=allergens,
        allergen_certain=True,
        is_vegetarian=True,
        is_vegan=True,
        is_pescatarian=True,
        is_complete=True,
        embedding=[0.1] * _DIM,
        ingredients=[
            Ingredient(position=i, name=name, raw_text=name, allergen_tags=[])
            for i, name in enumerate(ingredient_names)
        ],
    )
    recipe.nutrition = NutritionCache(
        basis_servings=2,
        calories=400,
        protein_g=20,
        carbs_g=50,
        fat_g=10,
        is_approximate=False,
        unmapped_ingredient_count=0,
    )
    session.add(recipe)
    session.flush()
    return recipe.id


@pytest.fixture
def planted(db_session: Session) -> dict[str, uuid.UUID]:
    """Seed one safe and one peanut dinner recipe — the peanut one is the violator to chase down."""
    safe = _add_recipe(
        db_session,
        source_id="reg-safe",
        title="Veg Stew",
        allergens=[],
        ingredient_names=["carrot", "potato"],
    )
    nut = _add_recipe(
        db_session,
        source_id="reg-nut",
        title="Peanut Curry",
        allergens=["peanuts"],
        ingredient_names=["peanut", "coconut milk"],
    )
    return {"safe": safe, "nut": nut}


async def _arrange_nut_cook_with_both_favorited(client, safe_id: str, nut_id: str) -> None:
    """Favorite BOTH recipes while unconstrained, then declare the peanut allergy.

    Favoriting the violator first gives the favorites path a real chance to leak it; the allergy is set
    afterwards so every path is then queried under the constraint the wall must enforce.
    """
    for rid in (safe_id, nut_id):
        resp = await client.post("/favorites", headers=_NUT_COOK, json={"recipe_id": rid})
        assert resp.status_code == 201
    resp = await client.put(
        "/profile",
        headers=_NUT_COOK,
        json={"diet": "none", "allergies": ["peanuts"], "default_servings": 2},
    )
    assert resp.status_code == 200


async def _surfaced_ids(client, path: str, ids: list[str]) -> set[str]:
    """Return the set of recipe ids the given cook-facing path surfaces to the nut-allergic cook.

    For the list paths that is the ids in the returned cards; for the detail path it is the ids that
    answer 200 (a withheld recipe answers 404, so it never appears). Same wall, three surfaces.
    """
    if path == "GET /recipes":
        resp = await client.get("/recipes", params={"category": "dinner"}, headers=_NUT_COOK)
        assert resp.status_code == 200
        return {c["id"] for c in resp.json()}
    if path == "GET /favorites":
        resp = await client.get("/favorites", headers=_NUT_COOK)
        assert resp.status_code == 200
        return {c["id"] for c in resp.json()}
    # GET /recipes/{id}: a recipe is "surfaced" only if its detail returns 200.
    surfaced: set[str] = set()
    for rid in ids:
        resp = await client.get(f"/recipes/{rid}", headers=_NUT_COOK)
        if resp.status_code == 200:
            surfaced.add(resp.json()["id"])
    return surfaced


@pytest.mark.parametrize("path", _PATHS)
async def test_no_path_surfaces_a_violating_recipe(make_user_client, planted, path) -> None:
    """For a nut-allergic cook, the peanut recipe surfaces on no cook-facing path; the safe one still does.

    The negative assertion is the grade (a leak here means the wall was bypassed); the positive assertion
    guards against a trivially-passing test that simply hides everything.
    """
    safe_id, nut_id = str(planted["safe"]), str(planted["nut"])
    async with make_user_client() as client:
        await _arrange_nut_cook_with_both_favorited(client, safe_id, nut_id)
        surfaced = await _surfaced_ids(client, path, [safe_id, nut_id])

    assert nut_id not in surfaced, f"{path} leaked a peanut recipe to a nut-allergic cook (wall bypassed)"
    assert safe_id in surfaced, f"{path} should still surface the compliant recipe"


async def test_rag_search_path_never_surfaces_a_violating_recipe(
    make_user_client, planted, monkeypatch
) -> None:
    """The intelligent search path (POST /chat → rag) also withholds the peanut recipe (T030).

    US1 added a new recipe surface — semantic search — that builds cards through `recipe_view`, so it
    inherits the wall. This pins that: with the hosted embedding/LLM mocked and the router forced to
    find_recipe, a nut-allergic cook's chat search returns the safe recipe and never the peanut one.
    """
    safe_id, nut_id = str(planted["safe"]), str(planted["nut"])
    # Mock the hosted providers: any query embeds to the planted recipes' vector, the reply is canned, and
    # the router is pinned to the find_recipe workflow so the turn exercises the rag path deterministically.
    monkeypatch.setattr(embeddings, "embed_query", lambda _text: [0.1] * _DIM)
    monkeypatch.setattr(
        llm_groq,
        "chat",
        lambda _messages, **_kwargs: type(
            "R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": "ok"})()})()]}
        )(),
    )
    monkeypatch.setattr(
        router_service, "route", lambda _message: IntentRoute("find_recipe", 0.99, "workflow")
    )

    async with make_user_client() as client:
        await _arrange_nut_cook_with_both_favorited(client, safe_id, nut_id)
        resp = await client.post("/chat", headers=_NUT_COOK, json={"message": "a dinner recipe"})

    assert resp.status_code == 200
    surfaced = {c["id"] for c in resp.json()["recipes"]}
    assert nut_id not in surfaced, "rag search leaked a peanut recipe to a nut-allergic cook (wall bypassed)"
    assert safe_id in surfaced, "rag search should still surface the compliant recipe"


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


async def test_agent_meal_plan_path_never_surfaces_a_violating_recipe(
    make_user_client, planted, monkeypatch
) -> None:
    """The bounded-agent meal-plan path withholds the peanut recipe from the plan AND its shopping list (T051).

    US3 added two more recipe surfaces — the agent's `search_recipes` tool and the assembled meal plan +
    shopping list — both of which build through `recipe_view`/`constraint_guard`, so they inherit the wall.
    This pins it: with the embedding/LLM mocked and the router forced to the plan_meals agent route, a
    nut-allergic cook's plan contains the safe recipe on no day, never the peanut one, and the consolidated
    shopping list never aggregates the peanut recipe's ingredients.
    """
    safe_id, nut_id = str(planted["safe"]), str(planted["nut"])
    monkeypatch.setattr(embeddings, "embed_query", lambda _text: [0.1] * _DIM)
    # Agent script: round 1 searches (surfacing the planted recipes through the wall), round 2 answers.
    responses = iter(
        [
            _tool_resp([_tool_call("c1", "search_recipes", json.dumps({"query": "dinners"}))]),
            _final_resp("Here's your plan."),
        ]
    )
    monkeypatch.setattr(llm_groq, "chat", lambda _messages, **_kwargs: next(responses))
    monkeypatch.setattr(
        router_service, "route", lambda _message: IntentRoute("plan_meals", 0.99, "agent")
    )

    async with make_user_client() as client:
        await _arrange_nut_cook_with_both_favorited(client, safe_id, nut_id)
        resp = await client.post("/chat", headers=_NUT_COOK, json={"message": "plan a few dinners"})

    assert resp.status_code == 200
    plan = resp.json()["meal_plan"]
    assert plan is not None
    day_ids = {day["recipe"]["id"] for day in plan["days"]}
    assert nut_id not in day_ids, "the meal-plan path put a peanut recipe on a day (wall bypassed)"
    assert safe_id in day_ids, "the safe recipe should still appear in the plan"
    # The single shopping list must not aggregate the peanut recipe's ingredients either.
    list_titles = {
        title for line in resp.json()["shopping_list"]["lines"] for title in line["from_recipes"]
    }
    assert "Peanut Curry" not in list_titles, "the shopping list aggregated a violating recipe (wall bypassed)"
