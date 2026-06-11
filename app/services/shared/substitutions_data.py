"""Curated ingredient-substitution map — the ONLY source of substitute suggestions (never invented).

Golden rule #2 (ground everything) applies to swaps too: the assistant never makes up a replacement.
Every suggestion comes from this committed table, and every substitute is annotated with the allergens
it would INTRODUCE so the substitution service can fail-closed — dropping any swap that carries an
allergen the cook declared (`app/services/user/substitution.py`). Allergen tokens are the canonical
`Allergen` enum values, identical to what the wall reads on recipes, so the two safety paths can never
diverge.

This table is the tuning lever: broaden coverage to help more cooks WITHOUT ever suggesting something
that introduces a declared allergen. Keys are normalized (lowercase, singular-ish) ingredient names;
lookup normalizes the cook's ingredient the same way before matching.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.recipe import Allergen

__all__ = ["Substitute", "SUBSTITUTIONS", "lookup"]


@dataclass(frozen=True)
class Substitute:
    """One curated replacement plus the allergens it would INTRODUCE (the fail-closed filter key).

    `introduces` is the set of `Allergen` values this swap carries; the substitution service excludes the
    swap whenever it intersects the cook's declared allergies. An empty set means the swap is allergen-free
    among the nine tracked allergens. Frozen so a curated row can't be mutated at runtime.
    """

    name: str
    introduces: frozenset[Allergen] = field(default_factory=frozenset)


# Canonical curated map: normalized ingredient name -> ordered list of plausible substitutes.
# Each substitute names the allergens it introduces so an allergic cook never receives an unsafe swap.
# Ordering is rough preference (most common/closest match first); the service preserves it after filtering.
SUBSTITUTIONS: dict[str, list[Substitute]] = {
    # Dairy (milk allergen) — offer dairy-free swaps plus dairy ones for cooks without a milk allergy.
    "butter": [
        Substitute("olive oil"),
        Substitute("coconut oil"),
        Substitute("vegetable oil"),
        Substitute("margarine", frozenset({Allergen.SOY})),
        Substitute("ghee", frozenset({Allergen.MILK})),
    ],
    "milk": [
        Substitute("oat milk"),
        Substitute("coconut milk"),
        Substitute("rice milk"),
        Substitute("soy milk", frozenset({Allergen.SOY})),
        Substitute("almond milk", frozenset({Allergen.TREE_NUTS})),
    ],
    "cream": [
        Substitute("coconut cream"),
        Substitute("cashew cream", frozenset({Allergen.TREE_NUTS})),
        Substitute("evaporated milk", frozenset({Allergen.MILK})),
    ],
    "sour cream": [
        Substitute("coconut yogurt"),
        Substitute("greek yogurt", frozenset({Allergen.MILK})),
    ],
    "yogurt": [
        Substitute("coconut yogurt"),
        Substitute("soy yogurt", frozenset({Allergen.SOY})),
        Substitute("greek yogurt", frozenset({Allergen.MILK})),
    ],
    "cheese": [
        Substitute("nutritional yeast"),
        Substitute("cashew cheese", frozenset({Allergen.TREE_NUTS})),
    ],
    "parmesan": [
        Substitute("nutritional yeast"),
    ],
    # Eggs (binding/leavening) — most baking swaps are allergen-free.
    "egg": [
        Substitute("flax egg"),
        Substitute("mashed banana"),
        Substitute("unsweetened applesauce"),
        Substitute("aquafaba"),
        Substitute("silken tofu", frozenset({Allergen.SOY})),
    ],
    "eggs": [
        Substitute("flax egg"),
        Substitute("mashed banana"),
        Substitute("unsweetened applesauce"),
        Substitute("aquafaba"),
    ],
    "mayonnaise": [
        Substitute("mashed avocado"),
        Substitute("hummus", frozenset({Allergen.SESAME})),
    ],
    # Wheat / gluten — flours and thickeners.
    "flour": [
        Substitute("rice flour"),
        Substitute("oat flour"),
        Substitute("cornstarch"),
        Substitute("almond flour", frozenset({Allergen.TREE_NUTS})),
        Substitute("all-purpose flour", frozenset({Allergen.WHEAT_GLUTEN})),
    ],
    "breadcrumbs": [
        Substitute("crushed cornflakes"),
        Substitute("rolled oats"),
        Substitute("panko", frozenset({Allergen.WHEAT_GLUTEN})),
    ],
    "soy sauce": [
        Substitute("coconut aminos"),
        Substitute("tamari", frozenset({Allergen.SOY})),
    ],
    "pasta": [
        Substitute("rice noodles"),
        Substitute("zucchini noodles"),
        Substitute("wheat pasta", frozenset({Allergen.WHEAT_GLUTEN})),
    ],
    # Nuts — when a cook wants a nut-free swap.
    "peanut butter": [
        Substitute("sunflower seed butter"),
        Substitute("tahini", frozenset({Allergen.SESAME})),
        Substitute("almond butter", frozenset({Allergen.TREE_NUTS})),
    ],
    "almond": [
        Substitute("sunflower seeds"),
        Substitute("pumpkin seeds"),
    ],
    "walnuts": [
        Substitute("sunflower seeds"),
        Substitute("pumpkin seeds"),
    ],
    # Fish / shellfish.
    "fish sauce": [
        Substitute("coconut aminos"),
        Substitute("soy sauce", frozenset({Allergen.SOY})),
    ],
    # Sweeteners / pantry (allergen-free swaps).
    "honey": [
        Substitute("maple syrup"),
        Substitute("agave nectar"),
    ],
    "sugar": [
        Substitute("maple syrup"),
        Substitute("honey"),
        Substitute("coconut sugar"),
    ],
    "buttermilk": [
        Substitute("oat milk with lemon juice"),
        Substitute("milk with lemon juice", frozenset({Allergen.MILK})),
    ],
}


def _normalize(ingredient: str) -> str:
    """Lowercase + collapse surrounding whitespace so lookup is robust to casing/padding.

    Keeps it deliberately simple (no stemming): the curated keys cover the common phrasings, and the
    substitution service also tries a singular fallback for a trailing 's'. Matching the same normalization
    on both the keys (at authoring time) and the cook's input keeps the table predictable.
    """
    return " ".join(ingredient.lower().split())


def lookup(ingredient: str) -> list[Substitute] | None:
    """Return the curated substitutes for `ingredient`, or None when it isn't in the table.

    Normalizes the input, tries an exact key, then a naive singular/plural fallback (strip/add a trailing
    's') so "eggs"/"egg" or "walnut"/"walnuts" both resolve. None means "no curated data" — the service
    turns that into an honest no-suggestion answer rather than inventing one.
    """
    key = _normalize(ingredient)
    if key in SUBSTITUTIONS:
        return SUBSTITUTIONS[key]
    # Naive singular/plural fallback so a trailing 's' difference still resolves to the curated row.
    if key.endswith("s") and key[:-1] in SUBSTITUTIONS:
        return SUBSTITUTIONS[key[:-1]]
    if f"{key}s" in SUBSTITUTIONS:
        return SUBSTITUTIONS[f"{key}s"]
    return None
