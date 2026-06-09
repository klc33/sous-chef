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

import uuid

import pytest
from app.models.recipe import Ingredient, NutritionCache, Recipe
from sqlalchemy.orm import Session

# A cook who will declare a peanut allergy; the wall must withhold the peanut recipe from every path.
_NUT_COOK = {"X-Profile-ID": "regression-nut"}

# The three cook-facing recipe paths the regression sweeps. Adding a new surfacing endpoint? Add it here.
_PATHS = ["GET /recipes", "GET /recipes/{id}", "GET /favorites"]


def _add_recipe(
    session: Session,
    *,
    source_id: str,
    title: str,
    allergens: list[str],
    ingredient_names: list[str],
) -> uuid.UUID:
    """Insert one complete, openable dinner recipe (with nutrition) and return its id.

    Diet flags are permissive so only the allergen dimension is under test; a NutritionCache row is
    attached so the recipe is reachable on the detail path (which 404s a recipe lacking nutrition).
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
