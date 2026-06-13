# Phase 1 Data Model: Corpus Data Quality

**No database migration.** Every persisted field this feature needs already exists. This document
describes the entities, their fields, and the validation/derivation rules the feature relies on, so the
contract and tasks stay grounded.

## Persisted entities (existing schema — unchanged)

### `recipes` (relevant fields)

| Field | Type | Notes |
|-------|------|-------|
| `id` | str | recipe identity |
| `title` | str | also used as image `alt` text |
| `category` | enum | one of `hot_drink \| cold_drink \| breakfast \| lunch \| dinner` (fixed; selects the placeholder) |
| `image_url` | str \| null | **source** image only (TheMealDB/TheCocktailDB). Null for Food.com/RecipeNLG. Never a placeholder URL. |
| `ingredients` | rows | parsed `name` / `quantity` / `unit` / `raw_text`; the unit of aggregation + scaling |

### `nutrition_cache` (relevant fields — one row per recipe)

| Field | Type | Rule |
|-------|------|------|
| `basis_servings` | int | NOT NULL, ≥ 1; the serving count totals are computed for |
| `calories` | numeric | total for `basis_servings`; ≥ 0 |
| `protein_g` / `carbs_g` / `fat_g` | numeric | totals for `basis_servings`; ≥ 0 |
| `is_approximate` | bool | `false` only for authoritative source nutrition (Food.com); **always `true`** for the aggregation path |
| `unmapped_ingredient_count` | int | ≥ 0; ingredients excluded from the totals; expresses "N of M" coverage |

**Invariants**
- Aggregation path ⇒ `is_approximate = true` by construction (averages, never exact), regardless of
  coverage.
- Authoritative path (`from_food_com`) ⇒ `is_approximate = false`, `unmapped_ingredient_count = 0`.
- `is_approximate` and `unmapped_ingredient_count` are **invariant under serving rescaling**.
- The backfill never sets `is_approximate` from `false` → `true` (it skips authoritative rows entirely).

## Curated reference data (committed static, not in DB)

### `NUTRIMENTS_PER_100G` — `dict[name → {energy-kcal_100g, proteins_100g, carbohydrates_100g, fat_100g}]`
USDA per-100g macros, **same field keys as the OFF adapter** so a fallback value is interchangeable with
a real OFF payload. Frequency-driven membership (R2). Lookup: normalize + singular/plural fallback.

### `ITEM_GRAMS` — `dict[name → grams]`
Average mass of one whole item (egg 50 g, onion 110 g, garlic clove 3 g) for count / unit-less lines.

### `COUNT_UNIT_GRAMS` — `dict[unit → grams]`
Generic mass for **stable** count units (clove, slice, pinch, dash, sprig). Variable units
(can/package/stick/piece) are deliberately absent ⇒ such lines stay unmapped.

### Food.com reference daily values (constants)
`total_fat = 65 g`, `protein = 50 g`, `carbs = 300 g` — fixed FDA pre-2016 DVs for PDV→grams (R1).

### Per-category placeholder assets — `widget/src/assets/placeholders/{category}.svg`
Exactly five, keyed on the canonical category value. Generic by design (never a specific dish).

## Derived / DTO surface

### `NutritionSummary` (read-time DTO, from `app/schemas/recipe.py`)
`servings`, `calories`, `protein_g`, `carbs_g`, `fat_g`, `is_approximate`,
`unmapped_ingredient_count (≥0, default 0)`. Produced by `services/user/nutrition.scale()` which scales
totals by `cook_servings / basis_servings` and carries the two coverage fields verbatim.

### Cook-facing nutrition state (derived in the view, not stored)
- **complete** — any macro > 0 and `unmapped_ingredient_count == 0`
- **partial** — any macro > 0 and `unmapped_ingredient_count > 0` → "estimated from N of M ingredients",
  M = rendered ingredient count, N = M − `unmapped_ingredient_count`
- **absent** — all macros == 0 → "nutrition isn't available"

### Image resolution (derived in the widget helper, not stored)
`imageFor(recipe) → { src, alt }`:
- `src` = `recipe.image_url` when present and loads; otherwise `placeholders/{recipe.category}.svg`
- `onError` on the `<img>` swaps `src` to the same category placeholder
- `alt` = `recipe.title` (names the dish; never empty)
