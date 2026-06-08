"""Nutrition scaling — turn a stored per-recipe NutritionCache row into the cook's serving size.

Totals are precomputed at ingestion for `basis_servings` (no live Open Food Facts calls on the request
path, per the no-runtime-external-calls rule). At read time we linearly rescale calories + macros from
that basis to the cook's chosen servings and carry `is_approximate` through unchanged, so the detail
view is honest about partial coverage. Pure and deterministic — no I/O.
"""

from __future__ import annotations

from app.models.recipe import NutritionCache
from app.schemas.recipe import NutritionSummary


def scale(nutrition: NutritionCache, cook_servings: int) -> NutritionSummary:
    """Rescale stored totals from their basis servings to the cook's servings.

    The factor is `cook_servings / basis_servings`; basis_servings is NOT NULL and >= 1 at ingestion, so
    the division is safe. Numeric columns come back as Decimal, hence the explicit float() coercions.
    `is_approximate` is passed through verbatim — scaling never makes an approximate number exact.
    """
    factor = cook_servings / nutrition.basis_servings
    return NutritionSummary(
        servings=cook_servings,
        calories=float(nutrition.calories) * factor,
        protein_g=float(nutrition.protein_g) * factor,
        carbs_g=float(nutrition.carbs_g) * factor,
        fat_g=float(nutrition.fat_g) * factor,
        is_approximate=nutrition.is_approximate,
    )
