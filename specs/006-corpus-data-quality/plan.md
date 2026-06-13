# Implementation Plan: Corpus Data Quality — Honest Nutrition & Images

**Branch**: `006-corpus-data-quality` | **Date**: 2026-06-13 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/006-corpus-data-quality/spec.md`

## Summary

Make every recipe surface tell the truth about two facts a cook acts on — nutrition and image — without
ever inventing one. Two halves:

- **Nutrition (already landed in code; this feature formalizes + verifies it).** A curated USDA
  FoodData Central fallback (`ingestion/ingredient_nutrition_data.py`) widens approximate aggregation
  where Open Food Facts returns nothing and resolves count / unit-less lines ("2 cloves garlic",
  "1 egg") to grams — strictly additive, always `is_approximate = true`. Recipes carrying Food.com
  RAW_recipes per-serving nutrition store **exact** macros via PDV→grams conversion against fixed FDA
  reference daily values. The cook-facing surfaces show three honest states (complete / "estimated from
  N of M ingredients" / "not available"), the chat reply mirrors the partial note, and an offline
  idempotent backfill recomputes existing rows without downgrading authoritative ones.
- **Images (the genuine remaining work).** Add committed per-category placeholder SVG assets and a
  small widget helper that picks the recipe's source `image_url` or, failing that, the placeholder for
  its fixed category. `RecipeCard` and `RecipeDetail` render an `<img>` with `alt = title` and an
  `onError` fallback to the same placeholder. No runtime image fetching, no schema change
  (`image_url` is already nullable), and never a third-party/stock photo presented as the dish
  (grounding).

**Implementation status going in** (verified against the tree on this branch): the nutrition half is
present and unit-tested; the chat reply already mirrors "Estimated from N of M ingredients"
([app/services/user/workflow.py:122](../../app/services/user/workflow.py#L122)); the OpenAPI contract
already carries `unmapped_ingredient_count` and a nullable `image_url`; the backfill and its repo
helpers (`iter_with_nutrition`, `set_nutrition`) exist; RUNBOOK + the ingestion data README already
document the Food.com swap and the fallback. The work this plan front-loads is therefore: the **image
placeholder system**, a focused **image-helper test**, and a **DECISIONS.md** record for the
image-grounding choice — plus a verification pass proving the already-landed nutrition behaviour matches
this (now formalized) spec.

## Technical Context

**Language/Version**: Python 3.12 (backend, ingestion, scripts); plain JavaScript/JSX (widget, no
TypeScript).

**Primary Dependencies**: FastAPI + Pydantic; SQLAlchemy + pgvector (via `app/repo`); pandas
(ingestion CSV load only); React + Vite (widget). No new dependency is added by this feature.

**Storage**: PostgreSQL. Relevant tables already exist: `recipes` (nullable `image_url`) and
`nutrition_cache` (`basis_servings`, `calories`, `protein_g`, `carbs_g`, `fat_g`, `is_approximate`,
`unmapped_ingredient_count`). **No migration** — the schema already holds every field this feature needs.

**Testing**: pytest (unit + integration + redteam); existing widget render is exercised manually via
`npm run dev`. New tests are unit-level (Python for the image-helper logic if placed server-side, or a
small JS assertion for the widget helper) plus the already-present nutrition fallback suite.

**Target Platform**: Linux containers via docker-compose; widget served as a static React app. Backfill
runs on the host against the mapped Postgres port.

**Project Type**: Web application — FastAPI monolith + React widget + offline ingestion/scripts, all in
one repo (per the constitution's single-monolith rule).

**Performance Goals**: No new request-path cost. Nutrition is precomputed at ingestion and only rescaled
at read time; images are static assets chosen client-side. Ingestion stays bounded (the Kaggle/Food.com
loader caps rows at ~1,500).

**Constraints**: Offline + idempotent ingestion and backfill (no live nutrition/image network calls); no
`torch`, no new runtime dependency, no image service; nutrition values only from an authoritative
reference or the recipe's own source; placeholders must read as clearly generic; the deterministic
allergy/diet wall and grounding are untouched.

**Scale/Scope**: Corpus on the order of a few thousand recipes; 5 fixed categories (one placeholder
each); macro fields only (calories + protein/carbs/fat).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment |
|-----------|------------|
| I. Simplicity Over Complexity | PASS — curated static data + a UI fallback + a maintenance script. No new service, store, or framework. |
| II. Build Only What Is Required | PASS — scope is exactly the spec's nutrition-honesty + image-fallback; the curated set is frequency-driven and bounded, not exhaustive. |
| III. Clear Separation of Concerns | PASS — DB access stays in `app/repo` (backfill calls `iter_with_nutrition`/`set_nutrition`); ingestion logic stays in `ingestion/`; presentation stays in the widget; layering unchanged. |
| IV. Testability | PASS — fallback tables, `_grams`, aggregate's USDA fallback, the always-approximate flag, and `scale()` passthrough are unit-tested; new image-helper resolution gets a unit test. Wall/grounding/redaction/red-team suites stay green and unchanged. |
| V. Reproducibility | PASS — ingestion + backfill are offline and idempotent; USDA values and placeholder SVGs are committed static assets; a clean run reproduces the corpus. |
| VI. Security & Privacy by Default | PASS — no new external calls, no new secrets, no new input surface; redaction path untouched. |
| VII. Maintainability | PASS — small single-purpose modules/assets; the image helper is one small function; no inline prompts touched. |
| VIII / IX. Documentation- & Spec-Driven | PASS — this plan + research/data-model/contracts/quickstart precede the remaining (image) code; DECISIONS.md records the image-grounding choice. Note: the nutrition code landed ahead of this spec and is being **formalized retroactively** here — the spec/plan are reconciled to the implementation, not the reverse. |
| X. No Unnecessary Technologies | PASS — no new tech; placeholders are static SVG, nutrition is static data, no image/nutrition API, no torch. |

**Non-negotiable safety invariants**: the wall and grounding remain deterministic and behaviourally
untouched (FR-020); nutrition values come only from an authoritative reference or source data, never a
model (FR-007); a placeholder is explicitly *not* a photo of the dish, preserving grounding. **No
violations — Complexity Tracking left empty.**

## Project Structure

### Documentation (this feature)

```text
specs/006-corpus-data-quality/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (recipe surface delta)
└── tasks.md             # Phase 2 output (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
ingestion/
├── ingredient_nutrition_data.py   # [DONE] curated USDA: NUTRIMENTS_PER_100G, ITEM_GRAMS, COUNT_UNIT_GRAMS
├── nutrition.py                   # [DONE] _grams count/unit-less resolution; aggregate USDA fallback; from_food_com PDV→g
├── fetch_kaggle.py                # [DONE] normalizes RecipeNLG OR Food.com RAW_recipes; parses 7-elem nutrition column
└── data/README.md                 # [DONE] documents Food.com preference; verify wording matches spec

app/
├── schemas/recipe.py              # [DONE] NutritionSummary.unmapped_ingredient_count (>=0, default 0)
├── services/user/nutrition.py     # [DONE] scale(): passes is_approximate + unmapped_ingredient_count verbatim
├── services/user/workflow.py      # [DONE] _nutrition_q mirrors "Estimated from N of M ingredients"
└── repo/recipes.py                # [DONE] iter_with_nutrition(), set_nutrition() used by the backfill

scripts/backfill_nutrition.py      # [DONE] offline idempotent recompute; skips authoritative rows; before/after report

widget/src/
├── lib/images.js                  # [NEW] imageFor(recipe) → {src, alt}: source image_url else category placeholder
├── assets/placeholders/*.svg      # [NEW] one generic SVG per fixed category (hot_drink, cold_drink, breakfast, lunch, dinner)
├── components/RecipeCard.jsx      # [CHANGE] <img alt={title}> via helper + onError → placeholder (today: blank div, alt="")
├── components/RecipeDetail.jsx    # [CHANGE] add the recipe image (today: none) via helper + onError → placeholder
└── styles.css                     # [CHANGE] consistent detail image sizing; placeholder object-fit

contracts/recipes.openapi.yaml     # [DONE] unmapped_ingredient_count + nullable image_url already present; verify only

docs/
├── DECISIONS.md                   # [CHANGE] add the image-grounding decision (generic placeholder over borrowed photo)
└── RUNBOOK.md                     # [DONE] backfill + Food.com refresh documented; verify only

tests/unit/
├── test_nutrition_fallback.py     # [DONE] tables + _grams + aggregate fallback + always-approximate
└── test_image_fallback.*          # [NEW] a recipe without image_url resolves to its category placeholder
```

**Structure Decision**: Existing monolith + widget layout is reused unchanged. The only new files are
the widget image helper, the per-category placeholder SVG assets, and one image-fallback test. Everything
nutrition-related already exists in its correct layer; this feature adds the image fallback beside it and
formalizes the whole through the SpecKit artifacts.

## Complexity Tracking

> No constitution violations — section intentionally empty.
