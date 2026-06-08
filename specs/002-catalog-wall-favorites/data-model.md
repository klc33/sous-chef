# Phase 1 Data Model: Recipe Catalog, the Safety Wall & Favorites

Six new tables, added in one Alembic migration (`0002_catalog`) on top of the `0001_baseline`
(`vector` extension is present but **unused** this phase). ORM models live in `app/models/recipe.py`
and `app/models/profile.py` and MUST be imported in `app/models/__init__.py` so Alembic autogenerate
sees them. All DB access goes through `app/repo/*`.

## Enumerations

- **Category** (exactly one per recipe): `hot_drink`, `cold_drink`, `breakfast`, `lunch`, `dinner`.
- **Allergen** (the nine supported): `peanuts`, `tree_nuts`, `milk`, `eggs`, `wheat_gluten`, `soy`,
  `fish`, `shellfish`, `sesame`.
- **Diet** (cook preference): `none`, `vegetarian`, `vegan`, `pescatarian`.
- **Source**: `themealdb`, `thecocktaildb`, `kaggle`.

Stored as constrained strings (Python `enum.StrEnum` + DB `CHECK`/native enum). Allergen sets are
stored as `text[]` columns.

## Tables

### `recipes`
The grounded recipe. Steps are stored verbatim and rendered as-is (never regenerated).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | surrogate key |
| `source` | Source | with `source_id` forms the idempotency key |
| `source_id` | text | unique with `source` (`UNIQUE(source, source_id)`) |
| `title` | text NOT NULL | |
| `category` | Category NOT NULL | the single metadata filter for browse |
| `cuisine` | text NULL | informational |
| `total_time_minutes` | int NULL | informational |
| `servings` | int NOT NULL | source servings; nutrition basis (default 1 if unknown) |
| `steps` | text[] NOT NULL | ordered, stored verbatim |
| `image_url` | text NULL | for cards |
| `allergens` | Allergen[] NOT NULL | union across ingredients (+OFF tags); may be empty |
| `allergen_certain` | bool NOT NULL | false ⇒ unrecognized ingredient ⇒ wall treats as possibly any allergen |
| `is_vegetarian` | bool NOT NULL | derived |
| `is_vegan` | bool NOT NULL | derived |
| `is_pescatarian` | bool NOT NULL | derived |
| `is_complete` | bool NOT NULL | true only when category + ≥1 ingredient + allergens + nutrition all present; **surfacing requires this** (FR-020) |
| `ingested_at` | timestamptz NOT NULL | |

Indexes: `UNIQUE(source, source_id)`; `INDEX(category, is_complete)` for browse.

### `ingredients`
Parsed ingredient lines for a recipe (provenance for allergens/nutrition + card "key ingredients").

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `recipe_id` | UUID FK → recipes(id) ON DELETE CASCADE | |
| `position` | int NOT NULL | display/order |
| `name` | text NOT NULL | normalized |
| `quantity` | numeric NULL | parsed where available |
| `unit` | text NULL | parsed where available |
| `raw_text` | text NOT NULL | original line (always retained) |
| `allergen_tags` | Allergen[] NOT NULL | per-ingredient matches (may be empty) |

Index: `INDEX(recipe_id)`.

### `nutrition_cache`
Per-recipe nutrition precomputed at ingestion (runtime reads + scales; **no live OFF calls**).

| Column | Type | Notes |
|---|---|---|
| `recipe_id` | UUID PK, FK → recipes(id) ON DELETE CASCADE | one row per recipe |
| `basis_servings` | int NOT NULL | servings the totals correspond to (= `recipes.servings`) |
| `calories` | numeric NOT NULL | total for basis_servings |
| `protein_g` | numeric NOT NULL | |
| `carbs_g` | numeric NOT NULL | |
| `fat_g` | numeric NOT NULL | |
| `is_approximate` | bool NOT NULL | true when any ingredient unmapped/unquantified |
| `unmapped_ingredient_count` | int NOT NULL | for transparency |

### `profiles`
The passwordless cook and their constraints. Keyed by the `X-Profile-ID` header value.

| Column | Type | Notes |
|---|---|---|
| `profile_id` | text PK | opaque client-generated id (never from request body) |
| `diet` | Diet NOT NULL | default `none` |
| `allergies` | Allergen[] NOT NULL | default empty |
| `default_servings` | int NOT NULL | default 2 |
| `created_at` | timestamptz NOT NULL | |
| `updated_at` | timestamptz NOT NULL | |

### `favorites`
Idempotent save of a recipe by a cook.

| Column | Type | Notes |
|---|---|---|
| `profile_id` | text FK → profiles(profile_id) ON DELETE CASCADE | part of PK |
| `recipe_id` | UUID FK → recipes(id) ON DELETE CASCADE | part of PK |
| `created_at` | timestamptz NOT NULL | |

Primary key `(profile_id, recipe_id)` ⇒ saving twice is a no-op (FR-018). Index `(profile_id)`.

### `seen_history` *(defined but inert this phase)*
Created per the planning input so the freshness phase can build on it. **No read/write behavior is
wired in this feature** (freshness is out of scope per the spec).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `profile_id` | text FK → profiles(profile_id) ON DELETE CASCADE | |
| `recipe_id` | UUID FK → recipes(id) ON DELETE CASCADE | |
| `shown_at` | timestamptz NOT NULL | |

Index `(profile_id, shown_at)`.

## Relationships

```
profiles 1───* favorites *───1 recipes
profiles 1───* seen_history *───1 recipes        (inert this phase)
recipes  1───* ingredients
recipes  1───1 nutrition_cache
```

## Constraint profile (runtime value object — not a table)

`constraint_guard` operates on a small resolved value built from a `profiles` row:
`ConstraintProfile(diet: Diet, allergies: set[Allergen])`. The wall's core predicate:

```
violates(recipe, cp) ==
    (cp.allergies ∩ recipe.allergens) is non-empty
    OR (cp.allergies is non-empty AND NOT recipe.allergen_certain)     # fail closed
    OR (cp.diet == vegan       AND NOT recipe.is_vegan)
    OR (cp.diet == vegetarian  AND NOT recipe.is_vegetarian)
    OR (cp.diet == pescatarian AND NOT recipe.is_pescatarian)
```

`cp.diet == none` never filters on diet. A recipe with `is_complete = false` is never a candidate
(excluded at the repo query level before the guard even runs).

## Validation rules (from requirements)

- `recipes.category` is NOT NULL and one of five (FR-009); browse filters by it exactly (FR-010, SC-005).
- `recipes.steps` rendered verbatim in detail (FR-013, SC-004).
- Surfacing queries select only `is_complete = true` (FR-020, SC-002).
- `favorites` PK enforces idempotency (FR-018).
- The guard runs on every cook-facing path via `recipe_view`, never bypassed (FR-004/FR-008/FR-019).
- Empty result is honest — no relaxation, no fabrication (FR-007, SC-007).
