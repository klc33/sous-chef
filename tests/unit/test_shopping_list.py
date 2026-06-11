"""Unit tests for the shopping-list aggregator (services/user/shopping_list.py) — US3, pure, no DB/LLM.

These pin the four guarantees FR-018..021 in isolation with tiny recipe stand-ins:
  * dedupe + merge compatible units (mass→grams, volume→ml) across recipes, scaled to the cook's servings;
  * split the SAME ingredient given in incompatible units (mass vs volume) into separate labelled lines;
  * carry an unquantified ingredient through as a None-quantity line (never a fabricated number);
  * always emit exactly ONE list, with `from_recipes` provenance per line.

`shopping_list.build` reads only `recipe.servings`, `recipe.title`, and each ingredient's
`name/quantity/unit`, so a SimpleNamespace recipe is a faithful, dependency-free stand-in.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.user import shopping_list


def _ing(name: str, quantity: float | None, unit: str | None) -> SimpleNamespace:
    """Build a minimal ingredient stand-in exposing exactly name/quantity/unit (what the aggregator reads)."""
    return SimpleNamespace(name=name, quantity=quantity, unit=unit)


def _recipe(title: str, servings: int, ingredients: list[SimpleNamespace]) -> SimpleNamespace:
    """Build a minimal recipe stand-in: a title (provenance), a serving basis (scaling), and ingredients."""
    return SimpleNamespace(title=title, servings=servings, ingredients=ingredients)


def _line_for(result: shopping_list.ShoppingList, ingredient: str, unit: str | None) -> object:
    """Return the single line matching (ingredient, unit), or fail loudly if it is missing/ambiguous."""
    matches = [
        line for line in result.lines if line.ingredient.lower() == ingredient.lower() and line.unit == unit
    ]
    assert len(matches) == 1, f"expected exactly one {ingredient!r} ({unit}) line, got {len(matches)}"
    return matches[0]


def test_exactly_one_list_returned() -> None:
    """The aggregator always returns a single ShoppingList object (FR-018 — one consolidated list)."""
    r = _recipe("Stew", 2, [_ing("carrot", 2, None)])
    result = shopping_list.build([r], cook_servings=2)
    assert isinstance(result, shopping_list.ShoppingList)


def test_dedupes_and_merges_compatible_mass_units() -> None:
    """Two recipes' flour (g and kg) merge into one grams line summed in the base unit (FR-019/020)."""
    r1 = _recipe("Bread", 2, [_ing("Flour", 200, "g")])
    r2 = _recipe("Cake", 2, [_ing("flour", 1, "kg")])  # 1 kg = 1000 g; name case-folds to merge

    result = shopping_list.build([r1, r2], cook_servings=2)  # servings == basis → factor 1

    line = _line_for(result, "Flour", "g")
    assert line.quantity == 1200.0  # 200 g + 1000 g
    assert set(line.from_recipes) == {"Bread", "Cake"}


def test_scales_to_cook_servings() -> None:
    """Quantities scale by cook_servings / recipe.servings before merging (FR-019)."""
    r = _recipe("Soup", 2, [_ing("Stock", 500, "ml")])
    result = shopping_list.build([r], cook_servings=4)  # 4/2 = 2x

    line = _line_for(result, "Stock", "ml")
    assert line.quantity == 1000.0


def test_incompatible_units_split_into_separate_lines() -> None:
    """The same ingredient in incompatible units (mass vs volume) stays as two labelled lines (FR-021)."""
    r1 = _recipe("Dish A", 1, [_ing("Milk", 100, "g")])
    r2 = _recipe("Dish B", 1, [_ing("Milk", 1, "cup")])

    result = shopping_list.build([r1, r2], cook_servings=1)

    mass_line = _line_for(result, "Milk", "g")
    vol_line = _line_for(result, "Milk", "ml")
    assert mass_line.quantity == 100.0
    assert vol_line.quantity == round(236.588, 2)  # 1 cup → ml


def test_unquantified_ingredient_becomes_none_quantity_line() -> None:
    """An ingredient with no quantity yields a None-quantity line — a checklist entry, never a guess."""
    r = _recipe("Salad", 2, [_ing("Salt", None, None)])
    result = shopping_list.build([r], cook_servings=2)

    line = _line_for(result, "Salt", None)
    assert line.quantity is None
    assert line.from_recipes == ["Salad"]


def test_count_units_merge_without_a_unit_label() -> None:
    """Unitless counts of one ingredient across recipes sum into a single None-unit line."""
    r1 = _recipe("Omelette", 2, [_ing("egg", 2, None)])
    r2 = _recipe("Cake", 2, [_ing("Egg", 3, None)])

    result = shopping_list.build([r1, r2], cook_servings=2)

    line = _line_for(result, "egg", None)
    assert line.quantity == 5.0
    assert set(line.from_recipes) == {"Omelette", "Cake"}
