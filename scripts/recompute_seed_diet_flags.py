"""One-time remediation: recompute the committed seed corpus' allergen/diet fields in place (T017a).

The canonical corpus path is `export_seed_corpus.py` from a populated dev DB. This script is the
network-free equivalent for fixing ONLY the allergen-derived fields on the already-committed artifact,
without re-embedding or re-fetching: diet flags and per-ingredient/recipe allergens are pure functions of
the ingredient names + their frozen allergen tags, and the embeddings do not depend on them — so
`embeddings.npy` and the row ORDER are left byte-identical and stay aligned.

It applies the corrected `ingestion.allergens` logic to each recipe in `seeds/corpus/recipes.jsonl`:
  * Open Food Facts false positives on trusted whole foods (garlic → "garlic bread" → milk) are stripped
    by recomputing those ingredients' tags from the keyword map alone.
  * Recipe `allergens` becomes the union of the cleaned per-ingredient tags.
  * Diet flags are re-derived via `derive_diet_flags`, so an animal allergen tag (incl. OFF-supplied milk)
    fails the matching diet closed and newly-recognized meat cuts (oxtail …) drop vegetarian/vegan.
`allergen_certain` is preserved as-is (it encodes the original OFF recognition we cannot replay offline,
and keeping it is the conservative, fail-closed choice). After this lands, re-run `load_seed_corpus.py`
against any target DB to propagate the corrected rows.

Run from the repo root:  uv run python -m scripts.recompute_seed_diet_flags
"""

from __future__ import annotations

import json
from pathlib import Path

from app.models.recipe import Allergen
from ingestion.allergens import _OFF_TRUSTED_SAFE, _keyword_allergens, _matches, derive_diet_flags

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RECIPES_PATH = _REPO_ROOT / "seeds" / "corpus" / "recipes.jsonl"


def _cleaned_tags(name: str, frozen: list[str]) -> set[Allergen]:
    """Return an ingredient's corrected allergen set: keyword-only for trusted foods, else the frozen set.

    Trusted whole foods get their tags recomputed from the keyword map alone, which drops the OFF
    false-positives baked into the committed artifact; every other ingredient keeps its frozen tags
    (keyword ∪ OFF), exactly as the corrected `analyze()` would now emit for the same OFF data.
    """
    if _matches(name.lower(), _OFF_TRUSTED_SAFE):
        return _keyword_allergens(name.lower())
    return {Allergen(tag) for tag in frozen}


def run() -> None:
    """Rewrite `recipes.jsonl` with corrected per-ingredient tags, recipe allergens, and diet flags."""
    rows = [json.loads(line) for line in _RECIPES_PATH.read_text(encoding="utf-8").splitlines() if line]

    changed_flags = 0
    changed_allergens = 0
    for row in rows:
        per_ingredient: list[tuple[str, set[Allergen]]] = []
        recipe_allergens: set[Allergen] = set()
        for ing in row["ingredients"]:
            tags = _cleaned_tags(ing["name"], ing.get("allergen_tags", []))
            ing["allergen_tags"] = sorted(t.value for t in tags)
            per_ingredient.append((ing["name"].lower(), tags))
            recipe_allergens |= tags

        new_allergens = sorted(a.value for a in recipe_allergens)
        if new_allergens != row.get("allergens"):
            changed_allergens += 1
        row["allergens"] = new_allergens

        flags = derive_diet_flags(per_ingredient, row["allergen_certain"])
        if any(row.get(k) != v for k, v in flags.items()):
            changed_flags += 1
        row.update(flags)

    # Re-serialize with the SAME options as the exporter (sorted keys, UTF-8, LF) for a clean diff.
    with _RECIPES_PATH.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    vegan = sum(1 for r in rows if r["is_vegan"])
    veg = sum(1 for r in rows if r["is_vegetarian"])
    print(
        f"recomputed {len(rows)} recipes: {changed_allergens} allergen-set changes, "
        f"{changed_flags} diet-flag changes; now {veg} vegetarian / {vegan} vegan."
    )


if __name__ == "__main__":
    run()
