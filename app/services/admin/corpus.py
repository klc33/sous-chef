"""Operator corpus inspection — read-only projection of ingested recipes for the dashboard.

Reads complete recipe rows through `repo/recipes` (the only DB-touching layer) and projects each into a
`RecipeCardAdmin` carrying the operator-relevant fields the cook card omits: provenance (source/source_id)
and the precomputed allergen union + diet flags the wall uses. This is inspection, not browsing-to-cook —
no wall filtering is applied because the operator is auditing the corpus itself, and the data is read-only.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.recipe import Recipe
from app.repo import recipes as repo_recipes
from app.schemas.admin import CorpusPage, RecipeCardAdmin

# Pager clamps (mirror contracts/admin.openapi.yaml: page>=1, 1<=page_size<=200).
_MIN_PAGE = 1
_MIN_PAGE_SIZE = 1
_MAX_PAGE_SIZE = 200


def _diet_flags(recipe: Recipe) -> list[str]:
    """Collect the diet labels a recipe satisfies from its precomputed boolean flags (order is stable)."""
    flags: list[str] = []
    if recipe.is_vegetarian:
        flags.append("vegetarian")
    if recipe.is_vegan:
        flags.append("vegan")
    if recipe.is_pescatarian:
        flags.append("pescatarian")
    return flags


def _to_admin_card(recipe: Recipe) -> RecipeCardAdmin:
    """Project one Recipe row into the operator-facing admin card (identity + provenance + safety tags)."""
    return RecipeCardAdmin(
        id=str(recipe.id),
        title=recipe.title,
        category=recipe.category,
        cuisine=recipe.cuisine,
        source=recipe.source,
        source_id=recipe.source_id,
        allergens=list(recipe.allergens),
        diet_flags=_diet_flags(recipe),
    )


def browse(
    session: Session,
    *,
    page: int = 1,
    page_size: int = 50,
    category: str | None = None,
) -> CorpusPage:
    """Return one clamped page of the corpus plus its total count for the dashboard pager.

    Clamps `page`/`page_size` to the contract bounds (so a malformed request degrades to a sane page rather
    than erroring), derives the offset, and asks the repo for both the page rows and the matching total. The
    optional `category` is an exact filter on the five fixed categories. Read-only: nothing here writes.
    """
    page = max(_MIN_PAGE, page)
    page_size = max(_MIN_PAGE_SIZE, min(_MAX_PAGE_SIZE, page_size))
    offset = (page - 1) * page_size

    rows = repo_recipes.list_page(session, limit=page_size, offset=offset, category=category)
    total = repo_recipes.count_complete(session, category=category)
    return CorpusPage(
        items=[_to_admin_card(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )
