# Phase 0 Research: Recipe Catalog, the Safety Wall & Favorites

The taxonomy, sources, and nutrition were locked in `/speckit-clarify`. The open items were
*integration mechanics* — how each public source behaves and how to derive categories, ingredients,
allergens, and nutrition deterministically and idempotently. Findings below.

## 1. Data sources & licensing

**Decision**: TheMealDB (food → breakfast/lunch/dinner), TheCocktailDB non-alcoholic (→ hot/cold
drink), a Kaggle subset (RecipeNLG or Food.com) for volume, Open Food Facts for nutrition+allergens.

- **TheMealDB / TheCocktailDB**: free JSON APIs. The free test key `1` is sufficient for development;
  endpoints support listing by category/letter and lookup by id. Each meal returns up to 20
  `strIngredientN` / `strMeasureN` pairs and `strInstructions` — exactly the verbatim steps we store.
  TheCocktailDB exposes `strAlcoholic` so we can keep only `Non alcoholic`.
- **Kaggle (RecipeNLG / Food.com)**: large CSVs, openly available for research. They are **not** fetched
  at build time — the chosen subset is downloaded manually into `ingestion/data/` (gitignored) and
  read by `fetch_kaggle.py`. Rationale: Kaggle needs auth, and we want reproducible offline runs.
- **Open Food Facts**: free, open database (ODbL). Provides per-product nutriments (energy/kcal,
  proteins, carbohydrates, fat per 100g) and an `allergens_tags` field. Usable via the product API or
  a downloaded dump.

**Rationale**: all public/free (constraint satisfied); APIs give clean ingredient/step structure for
food and drinks; Kaggle adds volume toward the ~2,000 target.
**Alternatives considered**: Spoonacular/Edamam (rejected — API-key-limited, not free at volume);
scraping recipe blogs (rejected — licensing + brittleness).

## 2. Category assignment (exactly one of five)

**Decision**: deterministic rule map applied at ingestion in `categorize.py`.
- TheCocktailDB → drinks only: classify **hot drink** vs **cold drink** by keyword cues in
  title/instructions/glass (e.g., "hot", "coffee", "tea", "mulled", "warm" → hot; default → cold).
- TheMealDB → food: map `strCategory`/tags to breakfast / lunch / dinner via a curated lookup
  (e.g., Breakfast→breakfast; Dessert/Side/Starter→lunch; Beef/Chicken/Lamb/Pasta/Seafood→dinner).
- Kaggle → use dataset tags/keywords with the same food lookup; when ambiguous, fall back to a
  single documented default (lunch) so the "exactly one category" invariant always holds.

**Rationale**: a committed lookup table is deterministic, auditable, and reproducible (SC-005), with no
runtime guessing. **Alternatives**: ML categorization (rejected — violates "no model at serve time" and
adds nondeterminism); per-recipe manual tagging (rejected — doesn't scale to ~2,000).

## 3. Ingredient parsing (name, qty, unit)

**Decision**: a small deterministic parser in `extract_ingredients.py` (stdlib `re` + a units list).
- TheMealDB/TheCocktailDB already separate ingredient name from measure; parse the measure into
  `(quantity, unit)` with a units whitelist; keep the original line as `raw_text`.
- Kaggle free-text lines: regex `^(qty)?\s*(unit)?\s*(name)$` against a known-units set; unparsed
  quantity/unit stay null but the name is still captured (`raw_text` always retained).

**Rationale**: no heavy NLP dependency; deterministic and debuggable; nulls are acceptable because the
spec only needs name + quantity/unit "where the source provides them." **Alternatives**: `ingredient-parser`
/ spaCy models (rejected — heavyweight, nondeterministic, against simplicity & lean-serving).

## 4. Allergen detection (deterministic, fail-closed)

**Decision**: a curated `allergen → ingredient-keyword` map (committed) is the primary signal, OFF
`allergens_tags` is a supplement, and a per-recipe **certainty flag** drives fail-closed behavior.
- For each parsed ingredient, match its normalized name against the keyword map for the nine allergens
  (peanuts, tree nuts, milk/dairy, eggs, wheat/gluten, soy, fish, shellfish, sesame).
- `recipes.allergens` = union of matched allergens across ingredients (∪ any OFF allergen tags).
- `recipes.allergen_certain = false` if **any** ingredient could not be recognized/normalized; this
  marks the recipe as possibly containing undetermined allergens.
- **Wall rule** (`constraint_guard`): for a cook allergic to X, exclude the recipe if `X ∈ allergens`
  **OR** `allergen_certain = false`. This is the fail-closed reading of FR-006 — uncertainty counts as
  a violation.

**Rationale**: deterministic, no model, auditable; aligns the wall with "never surface a violating
recipe." High ingredient-recognition coverage keeps `allergen_certain` mostly true, so the corpus stays
useful while staying safe. **Alternatives**: trusting source allergen fields alone (rejected — sparse
and inconsistent); LLM allergen tagging (rejected — nondeterministic, against the safety invariant).

> Tuning note: allergen-keyword coverage directly affects how many recipes are surfaceable to allergic
> cooks (uncertain → excluded). The ingestion `allergens.py` map is the lever; the red-team and
> integration tests assert zero violations, never a minimum surfaced count.

## 5. Diet classification (deterministic)

**Decision**: derive `is_vegetarian` / `is_vegan` / `is_pescatarian` at ingestion from the same
ingredient keyword signals (committed non-veg / animal-product / seafood keyword sets). `diet=none`
matches everything. Wall rule: a recipe satisfies a cook's diet only if the corresponding flag is true;
uncertainty (unrecognized ingredient) fails closed for stricter diets (vegan/vegetarian/pescatarian).

**Rationale**: same deterministic, auditable mechanism as allergens. **Alternatives**: source diet tags
(rejected — inconsistent), model classification (rejected — nondeterministic).

## 6. Nutrition derivation (Open Food Facts → calories + macros)

**Decision**: at ingestion, map each parsed ingredient to an OFF product (best-match on normalized name,
cached per ingredient name to avoid repeat lookups), take per-100g nutriments, scale by parsed
quantity→grams where determinable, and aggregate per recipe into `nutrition_cache` (calories, protein,
carbs, fat) for the recipe's source servings. Store `is_approximate = true` and an
`unmapped_ingredient_count` when any ingredient lacks a mapping or a usable quantity. **Runtime never
calls OFF** — `services/user/nutrition.py` only reads `nutrition_cache` and scales to the cook's
servings.

**Rationale**: precompute-and-cache keeps request paths lean and deterministic; "approximate" honestly
signals partial coverage (FR-015) instead of fabricating totals. **Alternatives**: live OFF calls at
request time (rejected — latency, rate limits, nondeterminism); USDA FDC (viable but a second API; OFF
already covers allergens+nutrition in one source).

## 7. Idempotent ingestion

**Decision**: every recipe carries `(source, source_id)` with a unique constraint; `load.py` upserts on
that key (insert-or-replace recipe + child ingredients + nutrition_cache in one transaction). Re-running
`run_ingest.py` converges to the same corpus without duplicates (P5 reproducibility).
**Alternatives**: truncate-and-reload (rejected — loses favorites' referential stability and is not
incremental); content-hash dedup only (rejected — source id is the natural stable key).

## 8. Profile-ID identity (passwordless)

**Decision**: requests carry `X-Profile-ID` (opaque client-generated id, e.g., a UUID stored in the
widget's localStorage). `api/deps.py` extracts and validates its presence; the owner is **never** read
from the request body. A profile row is created on first write (PUT /profile) or lazily defaulted
(diet=none, no allergies, servings=2) when first referenced. **Rationale**: matches the constitution's
passwordless identity and the foundation's tenant-from-header rule. **Alternatives**: cookie/session
auth (rejected — out of scope, no auth system), body-supplied owner (rejected — violates P6).

## Resolved unknowns

All planning-input items map to a decision above; no `NEEDS CLARIFICATION` remain. Deferred to later
phases (out of scope here): freshness/seen-history filtering, semantic/vector search, chat/agent,
meal-planning, shopping-list, substitutions.
