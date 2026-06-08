"""Deterministic allergen detection + diet derivation — the lever behind the fail-closed wall.

For each parsed ingredient we match its normalized name against a committed allergen→keyword map (the
primary signal) and supplement with Open Food Facts `allergens_tags`. The recipe's `allergens` is the
union; `allergen_certain` is False whenever ANY ingredient could not be recognized (matched to an
allergen, a known-safe keyword, or an OFF product) — which the wall reads as "possibly any allergen"
and excludes for allergic cooks (research §4). Diet flags are derived from the same keyword signals and
forced False when certainty is lost, so stricter diets also fail closed (research §5).

This map is the tuning lever: broaden coverage to make more recipes surfaceable to allergic cooks
WITHOUT ever weakening the wall. Tests assert zero violations, never a minimum surfaced count.
"""

from __future__ import annotations

from typing import Any

from app.infra.external.openfoodfacts import OpenFoodFacts
from app.models.recipe import Allergen

# Curated allergen → ingredient-keyword map (substring match against the normalized ingredient name).
ALLERGEN_KEYWORDS: dict[Allergen, tuple[str, ...]] = {
    Allergen.PEANUTS: ("peanut", "groundnut"),
    Allergen.TREE_NUTS: (
        "almond", "cashew", "walnut", "pecan", "pistachio", "hazelnut",
        "macadamia", "brazil nut", "pine nut", "chestnut", "nuts",
    ),
    Allergen.MILK: (
        "milk", "butter", "cheese", "cream", "yogurt", "yoghurt", "ghee",
        "custard", "curd", "whey", "casein", "parmesan", "mozzarella",
        "half-and-half", "half and half",
    ),
    Allergen.EGGS: ("egg", "mayonnaise", "meringue", "albumin"),
    Allergen.WHEAT_GLUTEN: (
        "wheat", "flour", "bread", "pasta", "noodle", "couscous", "barley",
        "rye", "semolina", "cracker", "breadcrumb", "spaghetti", "macaroni",
        "pastry",
    ),
    Allergen.SOY: ("soy", "soya", "tofu", "edamame", "miso", "tempeh"),
    Allergen.FISH: (
        "fish", "salmon", "tuna", "cod", "anchovy", "anchovies", "haddock",
        "sardine", "mackerel", "trout", "tilapia", "halibut", "worcestershire",
    ),
    Allergen.SHELLFISH: (
        "shrimp", "prawn", "crab", "lobster", "clam", "mussel", "oyster",
        "scallop", "squid", "crawfish", "crayfish",
    ),
    Allergen.SESAME: ("sesame", "tahini"),
}

# OFF allergen tag (language-prefix stripped already) → our Allergen.
_OFF_TAG_MAP: dict[str, Allergen] = {
    "peanuts": Allergen.PEANUTS,
    "nuts": Allergen.TREE_NUTS,
    "milk": Allergen.MILK,
    "eggs": Allergen.EGGS,
    "gluten": Allergen.WHEAT_GLUTEN,
    "soybeans": Allergen.SOY,
    "fish": Allergen.FISH,
    "crustaceans": Allergen.SHELLFISH,
    "molluscs": Allergen.SHELLFISH,
    "sesame-seeds": Allergen.SESAME,
}

# Meat/poultry keywords → not vegetarian, not pescatarian, not vegan.
_MEAT_POULTRY = (
    "beef", "chicken", "pork", "lamb", "bacon", "ham", "turkey", "duck",
    "veal", "goat", "sausage", "gelatin", "gelatine", "venison", "steak",
    "mince", "prosciutto", "salami", "pepperoni", "chorizo", "meat",
)
# Seafood keywords → not vegetarian/vegan, but pescatarian-compatible.
_SEAFOOD = ALLERGEN_KEYWORDS[Allergen.FISH] + ALLERGEN_KEYWORDS[Allergen.SHELLFISH]
# Animal-product (non-meat) keywords → not vegan (but still vegetarian).
_DAIRY = ALLERGEN_KEYWORDS[Allergen.MILK]
_EGGS = ALLERGEN_KEYWORDS[Allergen.EGGS]
_NON_VEGAN_OTHER = ("honey",)

# A broad known-safe vocabulary: common ingredients that carry none of the nine allergens. An ingredient
# matching one of these counts as "recognized" so it does not trip the fail-closed certainty flag.
_KNOWN_SAFE = (
    "water", "salt", "pepper", "sugar", "oil", "olive", "garlic", "onion",
    "tomato", "potato", "carrot", "rice", "bean", "lentil", "pea", "corn",
    "lettuce", "spinach", "cucumber", "lemon", "lime", "orange", "apple",
    "banana", "berry", "strawberry", "vanilla", "cinnamon", "ginger",
    "chili", "chilli", "paprika", "cumin", "coriander", "parsley", "basil",
    "thyme", "rosemary", "mint", "honey", "vinegar", "mustard", "stock",
    "broth", "wine", "juice", "chocolate", "cocoa", "coffee", "tea",
    "coconut", "avocado", "mushroom", "pepper", "celery", "cabbage",
    "broccoli", "cauliflower", "zucchini", "eggplant", "pumpkin", "squash",
    "yeast", "baking", "cornstarch", "syrup", "raisin", "date", "fig",
    "chickpea", "oat", "quinoa", "turmeric", "nutmeg", "clove", "bay",
    "leek", "shallot", "scallion", "bell", "kale", "beet", "radish",
    # Spices, herbs and aromatics (no allergen content) surfaced by the coverage analysis.
    "allspice", "dill", "marjoram", "saffron", "masala", "seasoning",
    "oregano", "sage", "cardamom", "fennel", "anise", "caraway", "sumac",
    "cayenne", "peppercorn", "tarragon", "chive", "cilantro", "lemongrass",
    "horseradish", "capers", "caper", "relish", "salsa", "sherry", "sriracha",
    # Produce / pantry items (no allergen content) surfaced by the coverage analysis.
    "jalapeno", "aubergine", "eggplant", "blueberry", "blueberries", "scotch",
    "bonnet", "fruit", "cranberry", "raspberry", "blackberry", "mango",
    "pineapple", "peach", "pear", "plum", "cherry", "grape", "melon", "kiwi",
    "spinach", "asparagus", "artichoke", "turnip", "parsnip", "okra", "chard",
    "currant", "apricot", "prune", "molasses", "gelatin", "agar", "pectin",
)


def _matches(name: str, keywords: tuple[str, ...]) -> bool:
    """Return True when any keyword appears as a substring of the normalized ingredient name."""
    return any(kw in name for kw in keywords)


def analyze(
    ingredients: list[dict[str, Any]], off: OpenFoodFacts | None = None
) -> dict[str, Any]:
    """Tag allergens per ingredient and derive recipe-level allergens, certainty, and diet flags.

    Mutates each ingredient dict to add `allergen_tags`. Returns a dict with the recipe-level
    `allergens` (sorted union), `allergen_certain`, and `is_vegetarian`/`is_vegan`/`is_pescatarian`.
    When OFF is supplied its allergen tags supplement the keyword map and an OFF hit also counts as
    recognition. Diet flags are forced False when certainty is lost (fail-closed for stricter diets).
    """
    recipe_allergens: set[Allergen] = set()
    certain = True
    has_meat_poultry = False
    has_seafood = False
    has_dairy = False
    has_eggs = False
    has_other_non_vegan = False

    for ing in ingredients:
        name = ing["name"].lower()
        tags: set[Allergen] = set()

        # Primary signal: the curated keyword map.
        for allergen, keywords in ALLERGEN_KEYWORDS.items():
            if _matches(name, keywords):
                tags.add(allergen)

        # Supplement: OFF allergen tags (cached lookup), if an adapter was provided.
        off_recognized = False
        if off is not None:
            payload = off.lookup_ingredient(ing["name"])
            for raw_tag in payload.get("allergen_tags", []):
                mapped = _OFF_TAG_MAP.get(raw_tag)
                if mapped is not None:
                    tags.add(mapped)
            off_recognized = bool(payload.get("allergen_tags") or payload.get("nutriments"))

        ing["allergen_tags"] = sorted(t.value for t in tags)
        recipe_allergens |= tags

        # Diet signals from the same normalized name.
        is_meat = _matches(name, _MEAT_POULTRY)
        if is_meat:
            has_meat_poultry = True
        if _matches(name, _SEAFOOD):
            has_seafood = True
        if _matches(name, _DAIRY):
            has_dairy = True
        if _matches(name, _EGGS):
            has_eggs = True
        if _matches(name, _NON_VEGAN_OTHER):
            has_other_non_vegan = True

        # Recognition: an allergen tag, a known meat/poultry, a known-safe keyword, or an OFF hit.
        # Meat/poultry carry no allergen tag yet are clearly identified, so they must count as
        # recognized — otherwise a plain "chicken" would wrongly flip the whole recipe to uncertain.
        recognized = bool(tags) or is_meat or _matches(name, _KNOWN_SAFE) or off_recognized
        if not recognized:
            certain = False

    # Derive diet flags; uncertainty forces them all False so stricter diets fail closed.
    is_vegetarian = certain and not has_meat_poultry and not has_seafood
    is_vegan = is_vegetarian and not has_dairy and not has_eggs and not has_other_non_vegan
    is_pescatarian = certain and not has_meat_poultry

    return {
        "allergens": sorted(a.value for a in recipe_allergens),
        "allergen_certain": certain,
        "is_vegetarian": is_vegetarian,
        "is_vegan": is_vegan,
        "is_pescatarian": is_pescatarian,
    }
