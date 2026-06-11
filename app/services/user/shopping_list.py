"""Shopping list — consolidate the ingredients of a meal plan into exactly ONE scaled, deduped list.

Pure and deterministic (no DB, no LLM): given the plan's recipe rows and the cook's serving target, it
produces a single `ShoppingList`. Three jobs, all in plain Python so the result is auditable (Principle
II / FR-018..021):

  * **Scale.** Each recipe's quantities are multiplied by `cook_servings / recipe.servings`, so the list
    matches how many people the cook is actually cooking for.
  * **Dedupe + merge.** Lines for the same ingredient are merged. Quantities only combine when their units
    are *compatible* — i.e. in the same measurement family (mass or volume), converted to a common base
    unit (grams / millilitres) before summing. A bare/unknown unit forms its own family so it never merges
    across incompatible kinds.
  * **Split incompatible units.** The same ingredient given in incompatible units (e.g. "2 cups" volume vs
    "100 g" mass) is kept as two separate, labelled lines rather than fabricating a bogus conversion.

Every line records `from_recipes` (the plan recipes it aggregates) so the cook can trace a quantity back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models.recipe import Recipe
from app.schemas.chat import ShoppingLine, ShoppingList

# Measurement-family conversion tables: each known unit maps to a factor toward the family's base unit.
# Within a family, quantities are summed in the base unit (grams for mass, millilitres for volume) and
# the merged line is presented in that base unit — an honest common denominator, not a guessed "nice" one.
_MASS_TO_GRAMS: dict[str, float] = {
    "g": 1.0, "gram": 1.0, "grams": 1.0,
    "kg": 1000.0, "kilogram": 1000.0, "kilograms": 1000.0,
    "oz": 28.3495, "ounce": 28.3495, "ounces": 28.3495,
    "lb": 453.592, "lbs": 453.592, "pound": 453.592, "pounds": 453.592,
}
_VOLUME_TO_ML: dict[str, float] = {
    "ml": 1.0, "milliliter": 1.0, "milliliters": 1.0, "millilitre": 1.0, "millilitres": 1.0,
    "l": 1000.0, "liter": 1000.0, "liters": 1000.0, "litre": 1000.0, "litres": 1000.0,
    "tsp": 4.92892, "teaspoon": 4.92892, "teaspoons": 4.92892,
    "tbsp": 14.7868, "tablespoon": 14.7868, "tablespoons": 14.7868,
    "cup": 236.588, "cups": 236.588,
}


@dataclass
class _Accumulator:
    """A mutable group-in-progress for one (ingredient, unit-family) bucket while aggregating lines.

    `total` is the running sum in the bucket's base unit (grams / millilitres / raw count); `has_quantity`
    records whether ANY merged line carried a quantity (so an all-unquantified group reports `None`, not 0).
    `display_name`/`display_unit` keep the first-seen labels, and `from_recipes` collects source titles.
    """

    display_name: str
    display_unit: str | None
    total: float = 0.0
    has_quantity: bool = False
    from_recipes: list[str] = field(default_factory=list)


def _normalize_name(name: str) -> str:
    """Return a lowercased, whitespace-collapsed key for an ingredient so trivial variants dedupe.

    Conservative on purpose: it folds case and spacing ("Olive  Oil" -> "olive oil") so the same
    ingredient merges, but does NOT stem plurals, which would risk wrongly fusing distinct ingredients.
    """
    return re.sub(r"\s+", " ", name.strip().lower())


def _classify_unit(unit: str | None) -> tuple[str, float, str | None]:
    """Map a raw unit to its (family-key, factor-to-base, display-unit) for compatible-unit merging.

    Mass and volume units collapse to shared families converting to grams / millilitres, so any two mass
    (or any two volume) quantities of one ingredient merge. A missing unit is the "count" family (summed
    as-is, no unit shown). Any other unit (e.g. "clove") becomes its OWN family keyed by the normalized
    word, so it merges with itself but never across an incompatible unit — that is the split guarantee.
    """
    if unit is None or not unit.strip():
        return ("count", 1.0, None)
    u = unit.strip().lower()
    if u in _MASS_TO_GRAMS:
        return ("mass", _MASS_TO_GRAMS[u], "g")
    if u in _VOLUME_TO_ML:
        return ("volume", _VOLUME_TO_ML[u], "ml")
    # Unknown unit: its own family so it self-merges but stays separate from mass/volume/count.
    return (f"unit:{u}", 1.0, u)


def build(recipes: list[Recipe], cook_servings: int) -> ShoppingList:
    """Aggregate the ingredients of `recipes` into exactly one deduplicated, serving-scaled ShoppingList.

    For each recipe, scales its ingredient quantities by `cook_servings / recipe.servings`, buckets every
    ingredient by (normalized name, unit family), and sums compatible quantities in a common base unit.
    Incompatible units for one ingredient land in different buckets and so emit separate labelled lines
    (FR-021). An ingredient with no parseable quantity yields a `None`-quantity line (the cook still gets
    a checklist entry). Bucket insertion order is preserved for a stable, testable list.
    """
    groups: dict[tuple[str, str], _Accumulator] = {}

    for recipe in recipes:
        # Guard a 0/None basis defensively (ingestion stores servings >= 1); fall back to 1 = no scaling.
        basis = recipe.servings if recipe.servings else 1
        factor = cook_servings / basis
        for ing in recipe.ingredients:
            family, to_base, display_unit = _classify_unit(ing.unit)
            key = (_normalize_name(ing.name), family)
            acc = groups.get(key)
            if acc is None:
                acc = _Accumulator(display_name=ing.name.strip(), display_unit=display_unit)
                groups[key] = acc
            if ing.quantity is not None:
                acc.total += float(ing.quantity) * factor * to_base
                acc.has_quantity = True
            if recipe.title not in acc.from_recipes:
                acc.from_recipes.append(recipe.title)

    lines = [
        ShoppingLine(
            ingredient=acc.display_name,
            # Round to tame float noise; None when the whole bucket was unquantified (honest "to taste").
            quantity=round(acc.total, 2) if acc.has_quantity else None,
            unit=acc.display_unit,
            from_recipes=acc.from_recipes,
        )
        for acc in groups.values()
    ]
    return ShoppingList(lines=lines)
