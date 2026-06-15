"""Integration tests for User Story 1 — account-owned data is isolated per cook (009, FR-005/SC-004).

Two seeded cooks each set a profile and save a favorite; the test drives the real authenticated endpoints
and asserts neither cook can read the other's favorites or profile. The owner key is the authenticated
cook account id (from the token), so the scoping the app already did per `X-Profile-ID` now holds per
account — proven here through the live HTTP surface, not a stand-in. (Seen-history isolation is covered by
`test_chat_flow.test_seen_history_is_per_cook`.)
"""

from __future__ import annotations

import uuid

import pytest
from app.models.recipe import Ingredient, Recipe
from sqlalchemy.orm import Session

# Two distinct cooks (owner keys); the `auth` fixture seeds an active cook per id and returns its headers.
_COOK_A = "iso-cook-a"
_COOK_B = "iso-cook-b"


def _add_recipe(session: Session, *, source_id: str, title: str) -> uuid.UUID:
    """Insert one complete, surfaceable, allergen-free dinner recipe and return its id."""
    recipe = Recipe(
        source="themealdb",
        source_id=source_id,
        title=title,
        category="dinner",
        servings=2,
        steps=["Mix.", "Serve."],
        allergens=[],
        allergen_certain=True,
        is_vegetarian=True,
        is_vegan=True,
        is_pescatarian=True,
        is_complete=True,
        ingredients=[Ingredient(position=0, name="carrot", raw_text="carrot", allergen_tags=[])],
    )
    session.add(recipe)
    session.flush()
    return recipe.id


@pytest.fixture
def two_recipes(db_session: Session) -> dict[str, uuid.UUID]:
    """Seed two safe dinner recipes — one each for cook A and cook B to favorite."""
    return {
        "a": _add_recipe(db_session, source_id="iso-a", title="Carrot Stew"),
        "b": _add_recipe(db_session, source_id="iso-b", title="Potato Bake"),
    }


async def test_favorites_are_isolated_per_cook(make_user_client, auth, two_recipes) -> None:
    """Each cook sees only the favorite they saved — neither leaks into the other's list (FR-005)."""
    rid_a, rid_b = str(two_recipes["a"]), str(two_recipes["b"])
    async with make_user_client() as client:
        assert (
            await client.post("/favorites", headers=auth(_COOK_A), json={"recipe_id": rid_a})
        ).status_code == 201
        assert (
            await client.post("/favorites", headers=auth(_COOK_B), json={"recipe_id": rid_b})
        ).status_code == 201

        favs_a = await client.get("/favorites", headers=auth(_COOK_A))
        favs_b = await client.get("/favorites", headers=auth(_COOK_B))

    assert [c["id"] for c in favs_a.json()] == [rid_a]
    assert [c["id"] for c in favs_b.json()] == [rid_b]


async def test_profiles_are_isolated_per_cook(make_user_client, auth) -> None:
    """Each cook reads back only their own stored constraints — one cook's PUT never alters another's."""
    async with make_user_client() as client:
        put_a = await client.put(
            "/profile",
            headers=auth(_COOK_A),
            json={"diet": "vegetarian", "allergies": ["peanuts"], "default_servings": 2},
        )
        put_b = await client.put(
            "/profile",
            headers=auth(_COOK_B),
            json={"diet": "vegan", "allergies": [], "default_servings": 4},
        )
        assert put_a.status_code == 200
        assert put_b.status_code == 200

        got_a = (await client.get("/profile", headers=auth(_COOK_A))).json()
        got_b = (await client.get("/profile", headers=auth(_COOK_B))).json()

    assert got_a["diet"] == "vegetarian"
    assert got_a["allergies"] == ["peanuts"]
    assert got_a["default_servings"] == 2
    assert got_b["diet"] == "vegan"
    assert got_b["allergies"] == []
    assert got_b["default_servings"] == 4
