"""Recipe data access — the ONLY place recipe rows are read or written (ORM/parameterized only).

`upsert_recipe` is the idempotent ingestion write (recipe + ingredients + nutrition in one transaction,
keyed on (source, source_id)). The read side — `list_by_category` and `get_by_id` — selects only
`is_complete = true` recipes for browse (the surfacing gate, FR-020) and eager-loads children so the
wall and recipe_view have everything they need without lazy-load surprises.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.recipe import Diet, Ingredient, NutritionCache, Recipe


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


def list_page(
    session: Session,
    *,
    limit: int,
    offset: int,
    category: str | None = None,
) -> list[Recipe]:
    """Return one page of complete recipes (optionally one category), ingredients eager-loaded.

    The read side of the operator corpus browse (admin, read-only inspection). Filters `is_complete = true`
    so incomplete rows never surface, applies the optional exact-category filter, and orders by title for a
    stable, pageable listing. `limit`/`offset` come pre-clamped from the service; ingredients are eager-loaded
    because the admin card lists allergen/diet tags derived from the row's precomputed columns.
    """
    stmt = (
        select(Recipe)
        .where(Recipe.is_complete.is_(True))
        .options(selectinload(Recipe.ingredients))
        .order_by(Recipe.title)
        .limit(limit)
        .offset(offset)
    )
    if category is not None:
        stmt = stmt.where(Recipe.category == category)
    return list(session.execute(stmt).scalars())


def count_complete(session: Session, *, category: str | None = None) -> int:
    """Return the total count of complete recipes (optionally one category) for the corpus pager.

    Matches the filter of `list_page` exactly (minus paging) so `total` and the returned page agree; a
    single `COUNT(*)` rather than loading rows, so the pager stays cheap on the ~2k-row corpus.
    """
    stmt = select(func.count()).select_from(Recipe).where(Recipe.is_complete.is_(True))
    if category is not None:
        stmt = stmt.where(Recipe.category == category)
    return int(session.execute(stmt).scalar_one())


def search_by_vector(
    session: Session,
    query_vec: list[float],
    *,
    category: str | None = None,
    diet: Diet = Diet.NONE,
    exclude_ids: list[uuid.UUID] | None = None,
    pool: int = 20,
) -> list[Recipe]:
    """Return the nearest complete, embedded recipes to `query_vec` as an OVER-FETCHED candidate pool.

    One parameterized cosine-distance query (`embedding <=> :query_vec`) with the cheap, exact predicates
    pushed into SQL: only complete + embedded rows, optionally one category, the cook's diet flag, and the
    seen-history exclusion. It deliberately returns up to `pool` rows (the `retrieval_candidate_pool`
    over-fetch, ~20) — NOT the final 3 — because the allergen wall (`constraint_guard`) trims afterward in
    the service layer; a hard `LIMIT 3` here could under-return when compliant recipes sit deeper in the
    ranking. Diet maps to the precomputed `is_vegetarian/is_vegan/is_pescatarian` flags (`Diet.NONE` never
    filters). Children are eager-loaded so the wall + recipe_view need no extra round-trips. ORM /
    parameterized only — `query_vec` and `exclude_ids` bind as parameters (injection-safe).
    """
    stmt = (
        select(Recipe)
        .where(Recipe.is_complete.is_(True), Recipe.embedding.isnot(None))
        .options(selectinload(Recipe.ingredients), selectinload(Recipe.nutrition))
        .order_by(Recipe.embedding.cosine_distance(query_vec))
        .limit(pool)
    )

    # Optional exact category pre-filter (the five fixed categories are a metadata filter, not a guess).
    if category is not None:
        stmt = stmt.where(Recipe.category == category)

    # Diet pre-filter: a stricter diet requires the matching precomputed flag; `none` adds no predicate.
    if diet == Diet.VEGAN:
        stmt = stmt.where(Recipe.is_vegan.is_(True))
    elif diet == Diet.VEGETARIAN:
        stmt = stmt.where(Recipe.is_vegetarian.is_(True))
    elif diet == Diet.PESCATARIAN:
        stmt = stmt.where(Recipe.is_pescatarian.is_(True))

    # Freshness: drop already-shown recipes for this cook (favorites are never added to that set).
    if exclude_ids:
        stmt = stmt.where(Recipe.id.notin_(exclude_ids))

    return list(session.execute(stmt).scalars())


def set_embedding(session: Session, recipe_id: uuid.UUID, embedding: list[float]) -> None:
    """Write one recipe's embedding vector (the offline ingestion embed-stage write).

    Kept in the repo so the embed stage never touches a session directly (DB access stays one layer).
    Flushes in the caller's transaction; a missing id is a no-op the caller already guards against.
    """
    recipe = session.get(Recipe, recipe_id)
    if recipe is not None:
        recipe.embedding = embedding
        session.flush()


def iter_embeddable(session: Session) -> list[Recipe]:
    """Return complete recipes that still need an embedding (null `embedding`), ingredients eager-loaded.

    The candidate set for the idempotent embed stage: re-running ingestion only embeds rows that lack a
    vector, so a rebuild converges without re-embedding the whole corpus. Ingredients are loaded because
    the embed text includes the first few ingredient names.
    """
    rows = session.execute(
        select(Recipe)
        .where(Recipe.is_complete.is_(True), Recipe.embedding.is_(None))
        .options(selectinload(Recipe.ingredients))
        .order_by(Recipe.ingested_at)
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
