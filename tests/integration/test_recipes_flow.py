"""Integration tests for User Story 1 — browse safe recipes by category.

Seeds compliant and violating recipes across several categories in a real DB, then drives the actual
HTTP path (GET /recipes) with the cook identified only by X-Profile-ID. Asserts the wall holds
end-to-end (a nut-allergic cook never sees a nut recipe), category purity (every returned card is in the
requested category, SC-005), the no-constraint cook sees everything, and an all-violating category
returns an honest empty list.
"""

from __future__ import annotations

import uuid

import pytest
from app.models.recipe import Ingredient, NutritionCache, Recipe
from sqlalchemy.orm import Session

# A cook with no stored profile / no constraints, and a nut-allergic cook.
_OPEN_COOK = {"X-Profile-ID": "cook-open"}
_NUT_COOK = {"X-Profile-ID": "cook-nut"}
# A cook whose stored profile sets servings to 4 (to exercise nutrition scaling on the detail path).
_SERVINGS_COOK = {"X-Profile-ID": "cook-4"}


def _add_recipe(
    session: Session,
    *,
    source_id: str,
    title: str,
    category: str,
    allergens: list[str],
    allergen_certain: bool = True,
    ingredient_names: list[str],
    steps: list[str] | None = None,
    nutrition: dict[str, object] | None = None,
    image_url: str | None = None,
) -> uuid.UUID:
    """Insert one complete, surfaceable recipe with its ingredients and return its id.

    Diet flags are left permissive (all True) so these fixtures probe the allergen/category behavior
    without diet interfering; `is_complete=True` makes the row a browse candidate. `steps` defaults to a
    short placeholder; pass an explicit list to assert verbatim rendering. When `nutrition` is given, a
    NutritionCache row is attached so the recipe is openable on the detail path.
    """
    recipe = Recipe(
        source="themealdb",
        source_id=source_id,
        title=title,
        category=category,
        servings=2,
        steps=steps if steps is not None else ["Mix.", "Serve."],
        allergens=allergens,
        allergen_certain=allergen_certain,
        is_vegetarian=True,
        is_vegan=True,
        is_pescatarian=True,
        is_complete=True,
        image_url=image_url,
        ingredients=[
            Ingredient(position=i, name=name, raw_text=name, allergen_tags=[])
            for i, name in enumerate(ingredient_names)
        ],
    )
    if nutrition is not None:
        recipe.nutrition = NutritionCache(**nutrition)
    session.add(recipe)
    session.flush()
    return recipe.id


@pytest.fixture
def seeded(db_session: Session) -> None:
    """Seed dinner/lunch/breakfast with a mix of compliant and nut-containing recipes."""
    # dinner: one safe, one with peanuts (violates a nut-allergic cook).
    _add_recipe(
        db_session,
        source_id="d-safe",
        title="Veg Stew",
        category="dinner",
        allergens=[],
        ingredient_names=["carrot", "potato", "onion", "celery", "thyme"],
    )
    _add_recipe(
        db_session,
        source_id="d-nut",
        title="Peanut Curry",
        category="dinner",
        allergens=["peanuts"],
        ingredient_names=["peanut", "coconut milk"],
    )
    # lunch: a single safe recipe.
    _add_recipe(
        db_session,
        source_id="l-safe",
        title="Tomato Soup",
        category="lunch",
        allergens=[],
        ingredient_names=["tomato", "basil"],
    )
    # breakfast: only a tree-nut recipe → empty for a nut-allergic cook.
    _add_recipe(
        db_session,
        source_id="b-nut",
        title="Almond Granola",
        category="breakfast",
        allergens=["tree_nuts"],
        ingredient_names=["almond", "oats"],
    )


async def _set_nut_allergy(client) -> None:
    """Set the nut-allergic profile (peanuts + tree nuts) via the real PUT /profile path."""
    resp = await client.put(
        "/profile",
        headers=_NUT_COOK,
        json={"diet": "none", "allergies": ["peanuts", "tree_nuts"], "default_servings": 2},
    )
    assert resp.status_code == 200


async def test_nut_cook_sees_only_compliant_cards(make_user_client, seeded) -> None:
    """A nut-allergic cook browsing dinner gets only the safe recipe — the peanut one is withheld."""
    async with make_user_client() as client:
        await _set_nut_allergy(client)
        resp = await client.get("/recipes", params={"category": "dinner"}, headers=_NUT_COOK)

    assert resp.status_code == 200
    body = resp.json()
    cards = body["items"]
    titles = {c["title"] for c in cards}
    assert titles == {"Veg Stew"}
    # The pager total counts only wall survivors (the peanut recipe is neither shown nor counted).
    assert body["total"] == 1
    # Cards carry the title + key ingredients (FR-011).
    (card,) = cards
    assert card["key_ingredients"] == ["carrot", "potato", "onion", "celery"]


async def test_category_purity(make_user_client, seeded) -> None:
    """Every returned card is in the requested category (SC-005) — no lunch recipe leaks into dinner."""
    async with make_user_client() as client:
        await _set_nut_allergy(client)
        resp = await client.get("/recipes", params={"category": "dinner"}, headers=_NUT_COOK)

    assert resp.status_code == 200
    cards = resp.json()["items"]
    assert cards  # non-empty
    assert all(c["category"] == "dinner" for c in cards)


async def test_open_cook_sees_all_in_category(make_user_client, seeded) -> None:
    """A cook with no constraints sees every recipe in the category, including the peanut one."""
    async with make_user_client() as client:
        resp = await client.get("/recipes", params={"category": "dinner"}, headers=_OPEN_COOK)

    assert resp.status_code == 200
    titles = {c["title"] for c in resp.json()["items"]}
    assert titles == {"Veg Stew", "Peanut Curry"}


async def test_all_violating_category_returns_empty(make_user_client, seeded) -> None:
    """A category whose only recipe violates the cook returns an honest empty list, not a substitute."""
    async with make_user_client() as client:
        await _set_nut_allergy(client)
        resp = await client.get("/recipes", params={"category": "breakfast"}, headers=_NUT_COOK)

    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_browse_is_paged(make_user_client, db_session: Session) -> None:
    """A category with more recipes than one page returns a page plus an honest total; later pages follow.

    Seeds 5 compliant lunch recipes and browses with page_size=2: the first page carries 2 cards with
    total=5, and walking to the last page returns the remaining 1. Cards never repeat across pages, and a
    page past the end is an honest empty slice (not an error), so the wall-then-slice order holds.
    """
    for i in range(5):
        _add_recipe(
            db_session,
            source_id=f"pg-{i}",
            title=f"Soup {i}",
            category="lunch",
            allergens=[],
            ingredient_names=["water", "salt"],
        )

    async with make_user_client() as client:
        first = await client.get(
            "/recipes",
            params={"category": "lunch", "page": 1, "page_size": 2},
            headers=_OPEN_COOK,
        )
        last = await client.get(
            "/recipes",
            params={"category": "lunch", "page": 3, "page_size": 2},
            headers=_OPEN_COOK,
        )
        past_end = await client.get(
            "/recipes",
            params={"category": "lunch", "page": 4, "page_size": 2},
            headers=_OPEN_COOK,
        )

    assert first.status_code == 200
    first_body = first.json()
    assert first_body["total"] == 5
    assert first_body["page"] == 1
    assert first_body["page_size"] == 2
    assert len(first_body["items"]) == 2

    last_body = last.json()
    assert len(last_body["items"]) == 1  # 5 recipes / page_size 2 → 1 left on page 3
    # No id appears on both the first and last page.
    assert {c["id"] for c in first_body["items"]}.isdisjoint({c["id"] for c in last_body["items"]})

    # A page beyond the end is an honest empty slice, still reporting the real total.
    past_body = past_end.json()
    assert past_body["items"] == []
    assert past_body["total"] == 5


async def test_missing_profile_id_is_rejected(make_user_client, seeded) -> None:
    """Browsing without an X-Profile-ID header is a 400 (identity comes from the header only)."""
    async with make_user_client() as client:
        resp = await client.get("/recipes", params={"category": "dinner"})

    assert resp.status_code == 400


# --- User Story 2: open a recipe for full instructions and nutrition -------------------------------


@pytest.fixture
def seeded_detail(db_session: Session) -> dict[str, uuid.UUID]:
    """Seed two openable dinner recipes (with nutrition): one safe, one peanut (violates a nut cook)."""
    safe = _add_recipe(
        db_session,
        source_id="x-safe",
        title="Lentil Bowl",
        category="dinner",
        allergens=[],
        ingredient_names=["lentil", "cumin"],
        steps=["Boil the lentils.", "Stir in cumin.", "Serve warm."],
        image_url="https://example.test/lentil-bowl.jpg",
        nutrition={
            "basis_servings": 2,
            "calories": 400,
            "protein_g": 20,
            "carbs_g": 50,
            "fat_g": 10,
            "is_approximate": True,
            "unmapped_ingredient_count": 1,
        },
    )
    nut = _add_recipe(
        db_session,
        source_id="x-nut",
        title="Peanut Noodles",
        category="dinner",
        allergens=["peanuts"],
        ingredient_names=["peanut", "noodle"],
        nutrition={
            "basis_servings": 2,
            "calories": 600,
            "protein_g": 25,
            "carbs_g": 70,
            "fat_g": 20,
            "is_approximate": False,
            "unmapped_ingredient_count": 0,
        },
    )
    return {"safe": safe, "nut": nut}


async def test_open_recipe_renders_verbatim_steps_and_scaled_nutrition(
    make_user_client, seeded_detail
) -> None:
    """Opening a card returns its stored steps verbatim and nutrition scaled to the cook's servings."""
    async with make_user_client() as client:
        # A cook who cooks for 4 — the stored servings drive nutrition scaling (basis 2 → factor 2).
        resp = await client.put(
            "/profile",
            headers=_SERVINGS_COOK,
            json={"diet": "none", "allergies": [], "default_servings": 4},
        )
        assert resp.status_code == 200
        resp = await client.get(f"/recipes/{seeded_detail['safe']}", headers=_SERVINGS_COOK)

    assert resp.status_code == 200
    body = resp.json()
    # Steps render exactly as stored — never rewritten (FR-013, SC-004).
    assert body["steps"] == ["Boil the lentils.", "Stir in cumin.", "Serve warm."]
    nut = body["nutrition"]
    assert nut["servings"] == 4
    assert nut["calories"] == 800  # 400 scaled from basis 2 to 4 servings
    assert nut["protein_g"] == 40
    assert nut["is_approximate"] is True  # passthrough, unchanged by scaling
    assert body["is_favorite"] is False
    # The recipe's own source photo round-trips so the detail view can show it (not just a placeholder).
    assert body["image_url"] == "https://example.test/lentil-bowl.jpg"


async def test_violating_recipe_detail_is_404(make_user_client, seeded_detail) -> None:
    """A recipe the wall withholds returns 404 on the detail path — no bypass, no existence leak."""
    async with make_user_client() as client:
        await _set_nut_allergy(client)
        resp = await client.get(f"/recipes/{seeded_detail['nut']}", headers=_NUT_COOK)

    assert resp.status_code == 404


async def test_unknown_recipe_id_is_404(make_user_client, seeded_detail) -> None:
    """A well-formed but unknown id is 404 — indistinguishable from a withheld recipe."""
    async with make_user_client() as client:
        resp = await client.get(f"/recipes/{uuid.uuid4()}", headers=_OPEN_COOK)

    assert resp.status_code == 404
