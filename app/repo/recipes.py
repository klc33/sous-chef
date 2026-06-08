"""Recipe data access — the ONLY place recipe rows are read or written (ORM/parameterized only).

`upsert_recipe` is the idempotent ingestion write (recipe + ingredients + nutrition in one transaction,
keyed on (source, source_id)). The read side — `list_by_category` and `get_by_id` — selects only
`is_complete = true` recipes for browse (the surfacing gate, FR-020) and eager-loads children so the
wall and recipe_view have everything they need without lazy-load surprises.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.recipe import Ingredient, NutritionCache, Recipe


def upsert_recipe(
    session: Session,
    *,
    source: str,
    source_id: str,
    title: str,
    category: str,
    servings: int,
    steps: list[str],
    allergens: list[str],
    allergen_certain: bool,
    is_vegetarian: bool,
    is_vegan: bool,
    is_pescatarian: bool,
    is_complete: bool,
    ingredients: list[dict[str, Any]],
    nutrition: dict[str, Any] | None,
    cuisine: str | None = None,
    total_time_minutes: int | None = None,
    image_url: str | None = None,
) -> Recipe:
    """Insert or replace a recipe and its children, idempotent on (source, source_id).

    Looks up any existing row for the key; updates its scalar fields and *replaces* its ingredients and
    nutrition (delete-orphan cascade removes the old children when the collections are reassigned). The
    whole thing flushes in the caller's transaction, so a re-run of ingestion converges to the same
    corpus without duplicates. Returns the persisted Recipe.
    """
    # Find the existing recipe for this natural key, if any.
    existing = session.execute(
        select(Recipe).where(Recipe.source == source, Recipe.source_id == source_id)
    ).scalar_one_or_none()

    recipe = existing if existing is not None else Recipe(source=source, source_id=source_id)

    # Assign/refresh the scalar columns either way.
    recipe.title = title
    recipe.category = category
    recipe.cuisine = cuisine
    recipe.total_time_minutes = total_time_minutes
    recipe.servings = servings
    recipe.steps = steps
    recipe.image_url = image_url
    recipe.allergens = allergens
    recipe.allergen_certain = allergen_certain
    recipe.is_vegetarian = is_vegetarian
    recipe.is_vegan = is_vegan
    recipe.is_pescatarian = is_pescatarian
    recipe.is_complete = is_complete

    # Replace children. Reassigning the mapped collections triggers delete-orphan for the old rows.
    recipe.ingredients = [
        Ingredient(
            position=ing["position"],
            name=ing["name"],
            quantity=ing.get("quantity"),
            unit=ing.get("unit"),
            raw_text=ing["raw_text"],
            allergen_tags=ing.get("allergen_tags", []),
        )
        for ing in ingredients
    ]
    recipe.nutrition = (
        NutritionCache(
            basis_servings=nutrition["basis_servings"],
            calories=nutrition["calories"],
            protein_g=nutrition["protein_g"],
            carbs_g=nutrition["carbs_g"],
            fat_g=nutrition["fat_g"],
            is_approximate=nutrition["is_approximate"],
            unmapped_ingredient_count=nutrition["unmapped_ingredient_count"],
        )
        if nutrition is not None
        else None
    )

    if existing is None:
        session.add(recipe)
    session.flush()
    return recipe


def list_by_category(session: Session, category: str) -> list[Recipe]:
    """Return complete recipes in one category, ingredients eager-loaded (the candidate set for browse).

    Filters `is_complete = true` so incomplete corpus rows never reach the wall; the (category,
    is_complete) index serves this exactly. Ordered by title for a stable listing.
    """
    rows = session.execute(
        select(Recipe)
        .where(Recipe.category == category, Recipe.is_complete.is_(True))
        .options(selectinload(Recipe.ingredients))
        .order_by(Recipe.title)
    ).scalars()
    return list(rows)


def get_by_id(session: Session, recipe_id: uuid.UUID) -> Recipe | None:
    """Fetch one recipe by id with ingredients + nutrition eager-loaded, or None if it does not exist.

    Does NOT filter on is_complete or constraints — visibility (404 vs detail) is the wall's job in
    recipe_view; the repo just returns the row so the caller can decide.
    """
    return session.execute(
        select(Recipe)
        .where(Recipe.id == recipe_id)
        .options(selectinload(Recipe.ingredients), selectinload(Recipe.nutrition))
    ).scalar_one_or_none()
