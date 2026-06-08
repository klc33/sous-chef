"""Integration tests for User Story 3 — save and revisit favorites.

Drives the real POST/GET/DELETE /favorites path against a real DB with the cook identified only by
X-Profile-ID. Asserts the lifecycle (save → list → remove), idempotent double-save (one entry), an
unknown recipe is rejected (404), persistence across a fresh client sharing the same profile-ID, and —
the key safety property — the wall on the list: a favorite saved before an allergy is declared drops out
of GET /favorites once that allergy is set via PUT /profile.
"""

from __future__ import annotations

import uuid

import pytest
from app.models.recipe import Ingredient, Recipe
from sqlalchemy.orm import Session

_COOK = {"X-Profile-ID": "cook-fav"}


def _add_recipe(
    session: Session,
    *,
    source_id: str,
    title: str,
    category: str = "dinner",
    allergens: list[str] | None = None,
    ingredient_names: list[str] | None = None,
) -> uuid.UUID:
    """Insert one complete, surfaceable recipe and return its id.

    Diet flags are permissive so these fixtures probe favorites + the allergen wall without diet
    interfering; `is_complete=True` makes the recipe a real surfaceable card. Allergens default to none.
    """
    recipe = Recipe(
        source="themealdb",
        source_id=source_id,
        title=title,
        category=category,
        servings=2,
        steps=["Mix.", "Serve."],
        allergens=allergens if allergens is not None else [],
        allergen_certain=True,
        is_vegetarian=True,
        is_vegan=True,
        is_pescatarian=True,
        is_complete=True,
        ingredients=[
            Ingredient(position=i, name=name, raw_text=name, allergen_tags=[])
            for i, name in enumerate(ingredient_names or ["carrot", "potato"])
        ],
    )
    session.add(recipe)
    session.flush()
    return recipe.id


@pytest.fixture
def safe_recipe(db_session: Session) -> uuid.UUID:
    """A nut-free dinner recipe a cook can safely favorite."""
    return _add_recipe(db_session, source_id="f-safe", title="Veg Stew")


@pytest.fixture
def peanut_recipe(db_session: Session) -> uuid.UUID:
    """A peanut dinner recipe — safe for an open cook, withheld once a nut allergy is declared."""
    return _add_recipe(
        db_session,
        source_id="f-nut",
        title="Peanut Curry",
        allergens=["peanuts"],
        ingredient_names=["peanut", "coconut milk"],
    )


async def test_save_list_remove_lifecycle(make_user_client, safe_recipe) -> None:
    """Saving returns 201, the recipe then appears in GET /favorites, and DELETE removes it."""
    async with make_user_client() as client:
        save = await client.post("/favorites", headers=_COOK, json={"recipe_id": str(safe_recipe)})
        assert save.status_code == 201

        listed = await client.get("/favorites", headers=_COOK)
        assert listed.status_code == 200
        assert [c["id"] for c in listed.json()] == [str(safe_recipe)]

        removed = await client.delete(f"/favorites/{safe_recipe}", headers=_COOK)
        assert removed.status_code == 204

        empty = await client.get("/favorites", headers=_COOK)
        assert empty.json() == []


async def test_double_save_is_idempotent(make_user_client, safe_recipe) -> None:
    """Saving the same recipe twice still yields exactly one favorite (idempotent, FR-018)."""
    async with make_user_client() as client:
        first = await client.post("/favorites", headers=_COOK, json={"recipe_id": str(safe_recipe)})
        second = await client.post("/favorites", headers=_COOK, json={"recipe_id": str(safe_recipe)})
        assert first.status_code == 201
        assert second.status_code == 201

        listed = await client.get("/favorites", headers=_COOK)
        assert [c["id"] for c in listed.json()] == [str(safe_recipe)]


async def test_save_unknown_recipe_is_404(make_user_client) -> None:
    """Saving a well-formed but non-existent recipe id is a 404 — no dangling favorite is created."""
    async with make_user_client() as client:
        resp = await client.post(
            "/favorites", headers=_COOK, json={"recipe_id": str(uuid.uuid4())}
        )
    assert resp.status_code == 404


async def test_favorites_persist_across_fresh_client(make_user_client, safe_recipe) -> None:
    """A favorite saved by one client is still listed by a fresh client with the same profile-ID."""
    async with make_user_client() as client:
        save = await client.post("/favorites", headers=_COOK, json={"recipe_id": str(safe_recipe)})
        assert save.status_code == 201

    # A brand-new client/session (same X-Profile-ID) — favorites are persisted, not in-memory.
    async with make_user_client() as fresh:
        listed = await fresh.get("/favorites", headers=_COOK)
    assert [c["id"] for c in listed.json()] == [str(safe_recipe)]


async def test_wall_omits_now_violating_favorite(make_user_client, peanut_recipe) -> None:
    """A peanut recipe favorited while unconstrained drops out of the list after a nut allergy is set."""
    async with make_user_client() as client:
        # Save while the cook has no constraints — the peanut recipe is currently surfaceable.
        save = await client.post(
            "/favorites", headers=_COOK, json={"recipe_id": str(peanut_recipe)}
        )
        assert save.status_code == 201
        assert [c["id"] for c in (await client.get("/favorites", headers=_COOK)).json()] == [
            str(peanut_recipe)
        ]

        # Declare a peanut allergy — the wall must now omit the saved peanut recipe from the list.
        put = await client.put(
            "/profile",
            headers=_COOK,
            json={"diet": "none", "allergies": ["peanuts"], "default_servings": 2},
        )
        assert put.status_code == 200

        listed = await client.get("/favorites", headers=_COOK)
    assert listed.status_code == 200
    assert listed.json() == []
