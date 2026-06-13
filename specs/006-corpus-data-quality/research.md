# Phase 0 Research: Corpus Data Quality

All four spec clarifications (2026-06-13 session) are resolved here, plus the image-fallback design
decisions. No open `NEEDS CLARIFICATION` remain.

## R1 — Food.com per-serving nutrition: PDV → grams, stored exact

**Decision.** When a Food.com RAW_recipes row carries its `nutrition` column, store macros as **exact**
(`is_approximate = false`). The column is `[calories, total_fat_PDV, sugar_PDV, sodium_PDV, protein_PDV,
sat_fat_PDV, carbs_PDV]`; calories are absolute kcal, the macros are percent-of-daily-value. Convert each
to grams with `grams = PDV/100 * DV` against the fixed pre-2016 FDA reference daily values the dataset
was built on: total fat 65 g, protein 50 g, carbohydrates 300 g.

**Rationale.** The conversion is deterministic and authoritative — a published constant, not a guess —
so the result is genuinely exact (clarification Q1, option A; SC-002). Calories need no conversion.

**Alternatives rejected.** (a) Store only calories exact and derive macros via the per-ingredient path —
needlessly discards the source's macros and makes them approximate (Q1 option B). (b) Flag the row
approximate to hedge the conversion — the conversion has no uncertainty, so the flag would be a false
signal (Q1 option C).

**Status.** Implemented in [ingestion/nutrition.py](../../ingestion/nutrition.py) `from_food_com` with
named DV constants `_DV_TOTAL_FAT_G`/`_DV_PROTEIN_G`/`_DV_CARBS_G`; the 7-tuple is validated in
[ingestion/fetch_kaggle.py](../../ingestion/fetch_kaggle.py) `_parse_nutrition`.

## R2 — Curated authoritative fallback set is frequency-driven

**Decision.** The curated USDA fallback (`NUTRIMENTS_PER_100G`, `ITEM_GRAMS`, `COUNT_UNIT_GRAMS`) is
populated from the **most-frequent unmapped** ingredient names and count units observed in the corpus
(top-N by occurrence), expanded until marginal coverage gain flattens — not an exhaustive food database
(clarification Q2, option A). Genuinely variable units (can/package/stick/piece) are deliberately omitted
so they stay honestly unmapped rather than guessed.

**Rationale.** Targets effort where it moves coverage most and stays reproducible from the operator's
coverage report. Bounding the set honours "build only what's required" (P2) and keeps the data auditable.

**Alternatives rejected.** (a) Fixed hand-picked pantry list independent of corpus frequency — may miss
the actual high-frequency misses. (b) Coverage-target-driven expansion to a fixed mapped rate — ties the
set size to a brittle global percentage this feature explicitly avoids (see R4).

**Status.** Tables implemented in
[ingestion/ingredient_nutrition_data.py](../../ingestion/ingredient_nutrition_data.py); lookups normalize
case/space and try a singular/plural fallback, mirroring `substitutions_data.py`.

## R3 — Backfill applies the new fallback to the existing corpus

**Decision.** The maintenance backfill recomputes nutrition for already-ingested recipes from their
**stored** ingredients using the on-disk OFF cache **and the new fallback logic**, so the existing corpus
gains coverage without a full re-ingest (clarification Q3, option A). It is offline and idempotent,
touches only the `nutrition_cache` row, and **skips authoritative (`is_approximate = false`) rows** so
exact data is never downgraded.

**Rationale.** Recompute is additive vs. the old logic, so a recipe's coverage can only improve or stay
equal; skipping exact rows makes a downgrade impossible by construction. A full Food.com re-ingest
(`make ingest`) remains the canonical, source-aware refresh.

**Alternatives rejected.** Re-running only the *old* computation — would not move the "no nutrition" rate
on a loaded corpus (Q3 option B).

**Status.** Implemented in [scripts/backfill_nutrition.py](../../scripts/backfill_nutrition.py); calls
`ingestion.nutrition.aggregate` over each recipe's stored ingredients and reports before/after all-zero
counts.

## R4 — Coverage improvement is operator-reported, not a hard CI gate

**Decision.** CI gates **per-case correctness** (the three nutrition states, the fallback behaviours, the
always-approximate flag, the image placeholder resolution). The overall coverage rise is **measured and
recorded by the operator** via the `ingestion/coverage` report before/after, not enforced as a numeric
floor (clarification Q4, option A).

**Rationale.** A global mapped-rate floor depends on corpus mix and would make CI flaky, tempting a
threshold weakening that golden rule #6 forbids. Per-case tests are stable and prove the behaviour that
matters; the coverage report still demonstrates the improvement for SC-001.

**Alternatives rejected.** (b) a committed numeric coverage gate, (c) both — both add a brittle,
corpus-dependent threshold for no behavioural guarantee.

## R5 — Data source: Food.com RAW_recipes over RecipeNLG

**Decision.** Prefer the Food.com RAW_recipes subset (per-line quantities + the 7-element per-serving
nutrition column) over RecipeNLG (ingredient **names only**, no quantities). The existing loader already
normalizes either schema, so the swap is operational (drop the right CSV) + documentation, not a code
change.

**Rationale.** RecipeNLG is nutrition-uncomputable from ingredients (no quantities), so its recipes can
only ever be "not available"; Food.com yields exact nutrition through the authoritative path and
scalable quantities through the parser. This is the single biggest lever on the "no nutrition" rate.

**Status.** [ingestion/fetch_kaggle.py](../../ingestion/fetch_kaggle.py) handles both schemas;
[ingestion/data/README.md](../../ingestion/data/README.md) and `docs/RUNBOOK.md` document the preference.
This feature only verifies the wording matches the spec.

## R6 — Image fallback: per-category committed placeholders, client-side, grounded

**Decision.** Render an image on every card and detail. A small widget helper picks the recipe's source
`image_url`; when it is absent (Food.com / RecipeNLG ship none), or when the `<img>` `onError` fires, it
falls back to a committed **per-category placeholder SVG** (one each for hot_drink, cold_drink,
breakfast, lunch, dinner). Every `<img>` uses `alt = recipe.title`. Placeholders are tasteful and
**clearly generic** — never a real or stock photo of a specific dish.

**Rationale.** Grounding (golden rule #2): a borrowed/stock/AI photo would assert a false fact about
*this* dish, while a generic category placeholder tells the truth that we have no photo. SVGs are tiny,
committed, theme-able, and need no runtime fetch or image service (P1/P10) — no new dependency. Choosing
client-side keeps the backend `image_url` honest (source-only) and avoids any schema change.

**Alternatives rejected.** (a) Fetch a stock/AI image at ingestion or runtime — violates grounding and
adds an image service + dependency. (b) A single generic placeholder for all categories — weaker UX and
loses the only honest signal (category) we can show. (c) Storing a placeholder URL in `image_url` —
pollutes the source field and risks a future code path treating it as a real photo.

**Implementation notes.** Helper at `widget/src/lib/images.js` keyed on the canonical category value
(matches `lib/categories.js`); SVGs under `widget/src/assets/placeholders/`. `RecipeCard` today renders a
blank `card__img--placeholder` div with `alt=""`; replace with the helper + `onError`. `RecipeDetail`
renders no image today; add one with the same helper. `styles.css` already sizes `.card__img`
(120px, object-fit cover); add matching detail sizing.

## R7 — Coverage states & honest copy (already implemented)

**Decision.** Three cook-facing states keyed off the stored row: **complete** (totals, no qualifier);
**partial** when `unmapped_ingredient_count > 0` → "estimated from N of M ingredients" where
M = rendered ingredient count and N = M − unmapped; **absent** ("nutrition isn't available") only when
every macro is zero. The chat `nutrition_q` reply mirrors the partial note.

**Rationale.** `is_approximate` and `unmapped_ingredient_count` are invariant under serving rescaling, so
`scale()` carries them verbatim — coverage is a property of the data, not the serving count.

**Status.** Implemented in [widget/src/components/RecipeDetail.jsx](../../widget/src/components/RecipeDetail.jsx)
and [app/services/user/workflow.py](../../app/services/user/workflow.py); verified against this spec.
