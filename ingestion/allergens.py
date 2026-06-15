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

import re
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
        # Species the small original list missed — a vegetarian was shown "orange roughy" and
        # "pilchards" because neither produced a fish tag (the wall trusts these flags). All are
        # collision-checked against the corpus so none mis-tags a safe food (e.g. "carp" is excluded
        # because it is a substring of "mascarpone"; "eel"/"ling" because of "peel"/"boiling").
        "roughy", "pilchard", "swordfish", "catfish", "monkfish", "snapper",
        "pollock", "pollack", "grouper", "barramundi", "branzino", "pomfret",
        "flounder", "whiting", "turbot", "plaice", "mahi", "bass", "perch",
        "bream", "herring", "kipper",
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

# Meat/poultry keywords → not vegetarian, not pescatarian, not vegan. Meat carries no top-9 allergen tag,
# so it is detected by name ALONE — meaning a missing cut (e.g. "oxtail") silently reads as vegan. Adding
# a term only ever makes diets STRICTER (fail-closed), so the list is curated broadly toward real meats;
# we still avoid short substrings that collide with vegan staples (e.g. "kidney" → kidney beans, "rib" →
# spare-rib vs nothing, "liver" → deliver) to keep genuinely-vegan recipes surfaceable.
_MEAT_POULTRY = (
    "beef", "chicken", "pork", "lamb", "bacon", "ham", "turkey", "duck",
    "veal", "goat", "sausage", "gelatin", "gelatine", "venison", "steak",
    "mince", "prosciutto", "salami", "pepperoni", "chorizo", "meat",
    # Cuts/forms previously missed (the "oxtail reads as vegan" bug, T017a) — all unambiguous meats.
    "oxtail", "brisket", "pancetta", "guanciale", "mortadella", "andouille",
    "kielbasa", "bratwurst", "frankfurter", "foie", "escargot", "snail",
    "quail", "pheasant", "rabbit", "tripe",
)
# Meat/animal-fat terms matched as WHOLE words, not substrings — for names that are a substring of a
# safe food. "lard" (pork/beef fat) is the motivating case: a bare substring match would wrongly flag
# "collard greens" as meat, so it is matched with word boundaries (catches "lard", not "collard").
_MEAT_WORDS = ("lard", "tallow", "suet", "schmaltz")

# Non-vegan, non-allergen animal product detected by name keyword (dairy/eggs/seafood now ride the
# allergen tags instead — see `derive_diet_flags`).
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


# Whole foods we trust to carry ZERO top-9 allergens. Open Food Facts is matched by free-text product
# search and routinely returns a *packaged* product for a bare ingredient (searching "garlic" matches
# "garlic bread", searching "salt" matches a seasoning blend), so its `allergens_tags` falsely tag these
# staples with milk/eggs/etc. For an ingredient whose name matches one of these, we IGNORE OFF allergen
# tags (the keyword map still applies, and OFF nutriments are still used) — this de-noises both the wall
# and the diet flags without ever dropping a real allergen, since these foods have none (T017a).
_OFF_TRUSTED_SAFE = (
    "water", "salt", "sugar", "garlic", "onion", "shallot", "scallion", "leek",
    "chive", "tomato", "potato", "carrot", "celery", "cucumber", "lettuce",
    "spinach", "kale", "chard", "cabbage", "broccoli", "cauliflower", "zucchini",
    "courgette", "eggplant", "aubergine", "pumpkin", "squash", "beet", "radish",
    "turnip", "parsnip", "okra", "asparagus", "artichoke", "mushroom",
    "bell pepper", "jalapeno", "chili", "chilli", "corn", "pea ", "peas",
    "lemon", "lime", "orange", "apple", "banana", "berry", "strawberry",
    "blueberry", "raspberry", "blackberry", "cranberry", "currant", "grape",
    "melon", "mango", "pineapple", "peach", "pear", "plum", "cherry", "kiwi",
    "apricot", "fig", "date", "raisin", "prune", "ginger", "turmeric", "cumin",
    "paprika", "cinnamon", "nutmeg", "clove", "bay leaf", "cardamom", "fennel",
    "anise", "caraway", "sumac", "cayenne", "peppercorn", "tarragon",
    "marjoram", "saffron", "allspice", "oregano", "basil", "thyme", "rosemary",
    "sage", "dill", "mint", "parsley", "cilantro", "coriander", "lemongrass",
    "horseradish",
)


def _matches(name: str, keywords: tuple[str, ...]) -> bool:
    """Return True when any keyword appears as a substring of the normalized ingredient name."""
    return any(kw in name for kw in keywords)


def _matches_word(name: str, words: tuple[str, ...]) -> bool:
    """Return True when any term matches the name as a WHOLE word (boundary-anchored, not a substring).

    Used for collision-prone terms like "lard" that are substrings of safe foods ("collard greens"):
    a `\\b`-anchored search matches the standalone ingredient but never the inner letters of another word.
    """
    return any(re.search(rf"\b{re.escape(w)}\b", name) for w in words)


def _is_meat(name: str) -> bool:
    """Return True when the ingredient is meat/poultry/animal-fat — substring list ∪ whole-word list.

    The single meat predicate so the diet derivation and the recognition check stay in lockstep: a name
    counts as meat if it matches the broad `_MEAT_POULTRY` substrings OR a boundary-anchored `_MEAT_WORDS`
    term (lard/tallow/suet/schmaltz), which must not also flag safe foods that merely contain those letters.
    """
    return _matches(name, _MEAT_POULTRY) or _matches_word(name, _MEAT_WORDS)


def _keyword_allergens(name: str) -> set[Allergen]:
    """Return the allergens whose keyword map matches the normalized ingredient name (no OFF)."""
    return {a for a, kws in ALLERGEN_KEYWORDS.items() if _matches(name, kws)}


def derive_diet_flags(
    per_ingredient: list[tuple[str, set[Allergen]]], certain: bool
) -> dict[str, bool]:
    """Derive the three diet flags from each ingredient's (name, final allergen tags) + recipe certainty.

    Animal-derived allergen TAGS are the diet signal for dairy/eggs/seafood — not a separate keyword pass —
    so a tag added by EITHER the keyword map OR Open Food Facts fails the matching diet closed (this fixes
    the bug where an OFF-only milk tag left a recipe flagged vegan). Meat/poultry and honey carry no top-9
    allergen, so they stay name-keyword signals. Uncertainty forces every flag False so stricter diets fail
    closed. Shared by `analyze()` (live ingest) and the seed-corpus regenerator so both stay identical.
    """
    has_meat_poultry = any(_is_meat(name) for name, _ in per_ingredient)
    has_seafood = any(
        Allergen.FISH in tags or Allergen.SHELLFISH in tags for _, tags in per_ingredient
    )
    has_dairy = any(Allergen.MILK in tags for _, tags in per_ingredient)
    has_eggs = any(Allergen.EGGS in tags for _, tags in per_ingredient)
    has_other_non_vegan = any(_matches(name, _NON_VEGAN_OTHER) for name, _ in per_ingredient)

    is_vegetarian = certain and not has_meat_poultry and not has_seafood
    is_vegan = is_vegetarian and not has_dairy and not has_eggs and not has_other_non_vegan
    is_pescatarian = certain and not has_meat_poultry
    return {
        "is_vegetarian": is_vegetarian,
        "is_vegan": is_vegan,
        "is_pescatarian": is_pescatarian,
    }


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
    per_ingredient: list[tuple[str, set[Allergen]]] = []

    for ing in ingredients:
        name = ing["name"].lower()

        # Primary signal: the curated keyword map.
        tags: set[Allergen] = _keyword_allergens(name)

        # Supplement: OFF allergen tags (cached lookup), if an adapter was provided — but NOT for trusted
        # whole foods, where OFF's product search returns false positives (garlic → "garlic bread" → milk).
        off_recognized = False
        if off is not None:
            payload = off.lookup_ingredient(ing["name"])
            if not _matches(name, _OFF_TRUSTED_SAFE):
                for raw_tag in payload.get("allergen_tags", []):
                    mapped = _OFF_TAG_MAP.get(raw_tag)
                    if mapped is not None:
                        tags.add(mapped)
            off_recognized = bool(payload.get("allergen_tags") or payload.get("nutriments"))

        ing["allergen_tags"] = sorted(t.value for t in tags)
        recipe_allergens |= tags
        per_ingredient.append((name, tags))

        # Recognition: an allergen tag, a known meat/poultry, a known-safe keyword, or an OFF hit.
        # Meat/poultry carry no allergen tag yet are clearly identified, so they must count as
        # recognized — otherwise a plain "chicken" would wrongly flip the whole recipe to uncertain.
        recognized = (
            bool(tags)
            or _is_meat(name)
            or _matches(name, _KNOWN_SAFE)
            or off_recognized
        )
        if not recognized:
            certain = False

    # Derive diet flags from the final (keyword ∪ trusted-filtered OFF) tags; uncertainty fails them closed.
    flags = derive_diet_flags(per_ingredient, certain)

    return {
        "allergens": sorted(a.value for a in recipe_allergens),
        "allergen_certain": certain,
        **flags,
    }
