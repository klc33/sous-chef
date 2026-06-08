# Implementation Plan: Recipe Catalog, the Safety Wall & Favorites

**Branch**: `002-catalog-wall-favorites` | **Date**: 2026-06-08 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/002-catalog-wall-favorites/spec.md`

## Summary

Build the first cook-facing product on the foundation skeleton: a real recipe **corpus**, the
deterministic **safety wall**, and the **non-AI surface** (category browse → cards → detail+nutrition,
plus favorites). An offline ingestion pipeline pulls public/free recipes (TheMealDB, TheCocktailDB
non-alcoholic, a Kaggle subset), assigns each exactly one of five categories, parses ingredients,
detects the nine supported allergens, derives Open Food Facts nutrition, and stores the result.

Technical approach: add the product schema via Alembic + SQLAlchemy ORM (`recipes`, `ingredients`,
`nutrition_cache`, `profiles`, `favorites`, `seen_history`). The DB is reached **only** through
`app/repo/*` (reusing the foundation `Database` adapter). All recipe rows are turned into cook-facing
DTOs **only** through `services/shared/recipe_view.py`, which **requires** the cook's constraints and
runs them through `services/user/constraint_guard.py` — a single deterministic choke point so no output
path can return a recipe without the wall. Thin `api/user/{recipes,favorites,profile}.py` routers read
the passwordless profile-ID from `api/deps.py`. No LLM, no embeddings, no semantic search in this phase
(category is a deterministic metadata filter).

## Technical Context

**Language/Version**: Python 3.12 (`requires-python >= 3.11`); image base `python:3.12-slim`.

**Primary Dependencies**: Runtime (`backend` extra, already present): FastAPI, SQLAlchemy, Alembic,
`psycopg[binary]`, Pydantic. **No new runtime deps** — the wall and surface are plain Python + SQL, and
nutrition/allergens are precomputed at ingestion (no live external calls at request time). Offline
ingestion (new `ingestion` dependency group, never shipped): `httpx` (base) for TheMealDB / TheCocktailDB
/ Open Food Facts, `pandas` for subsetting the Kaggle CSV. No torch, ever.

**Storage**: PostgreSQL 16 (+pgvector already enabled by the `0001_baseline` migration; **no vector
columns used this phase**). New tables added via one Alembic migration. Redis unused by this feature.

**Testing**: pytest. Unit: `constraint_guard` (incl. the "new output path forgets the guard"
regression) and shopping-list-style math for nutrition scaling; favorites persistence. Integration:
the three cook paths against a seeded DB, asserting the wall holds end-to-end.

**Target Platform**: Linux containers via docker-compose locally; Railway for the deployed backend.
Ingestion runs as an offline job (`make ingest` → `python -m ingestion.run_ingest`), not in the image.

**Project Type**: Single FastAPI monolith (per `projectplanFolderForMd/structure.md`), plus the
offline `ingestion/` pipeline.

**Performance Goals**: A category listing returns within ~1s for the corpus size (≤~2,000 recipes);
the category filter is an indexed metadata lookup, not a scan or a model call.

**Constraints**: The wall is deterministic code on EVERY output path (SC-001); grounding — steps render
verbatim, never invented (SC-004); 100% of *surfaceable* recipes have category + parsed ingredients +
allergen tags + nutrition (SC-002); favorites persist per profile-ID across sessions (SC-003); cook
identity from the profile-ID header only, never request body.

**Scale/Scope**: ~hundreds–2,000 recipes; 6 tables; 3 repos of read/write queries; 3 services
(guard, nutrition, favorites) + recipe_view DTOs; 3 thin routers; 1 ingestion pipeline (5 stages).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | How this plan complies |
|---|---|---|
| I Simplicity | PASS | Deterministic SQL category filter + Python guard; no vector/semantic search; reuse foundation adapters; one migration. |
| II Build only required | PASS | Only the spec's stories. `seen_history` table+repo are created (explicitly in scope per the planning input) but left **inert** — no freshness behavior, which is a later phase. |
| III Separation of concerns | PASS | `api → services → repo → infra` strict; only `repo/`+Alembic touch the DB; `infra/external/*` (TheMealDB/OFF) used **only** by offline ingestion; services split under `services/user/` + `services/shared/`. |
| IV Testability | PASS | `constraint_guard` unit-tested incl. the new-output-path regression; favorites persistence tested; adapters mockable; integration tests gate the three paths. |
| V Reproducibility | PASS | Schema via Alembic migration; ingestion is **idempotent** (upsert on `(source, source_id)`); deps pinned in `uv.lock`; corpus rebuildable via `make ingest`. |
| VI Security & privacy | PASS | ORM/parameterized queries only (injection-safe); profile-ID taken from header via `deps.py`, never from body; no secrets added; redaction already in place. No LLM input path in this feature. |
| VII Maintainability | PASS | Small single-purpose files matching `structure.md`; every function commented; lint + mypy. |
| VIII Documentation-first | PASS | This plan + research/data-model/contracts/quickstart precede code. |
| IX Spec-driven | PASS | Generated through the SpecKit cycle; artifacts committed on-branch. |
| X No unnecessary tech | PASS | No new runtime deps; ingestion adds only offline `pandas`; no torch, no vector DB, no auth system. |

**Safety invariants**:
- **The wall is the grade** — enforced as a single choke point (`recipe_view` requires constraints →
  calls `constraint_guard`); the regression test fails if any cook-facing recipe path skips it.
- **Ground everything** — cards/detail come only from stored rows; steps render verbatim; no safe match
  → honest empty list / 404, never fabricated.
- **Hosted inference only / lean serving** — not exercised; this feature uses no model at all.

**Result**: PASS — no violations; Complexity Tracking left empty.

## Project Structure

### Documentation (this feature)

```text
specs/002-catalog-wall-favorites/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── recipes.openapi.yaml    # GET /recipes, GET /recipes/{id}
│   ├── favorites.openapi.yaml  # POST/GET/DELETE /favorites
│   └── profile.openapi.yaml    # GET/PUT /profile
└── checklists/
    └── requirements.md  # spec quality checklist (from /speckit-specify)
```

### Source Code (repository root)

Fills these existing placeholders (per `projectplanFolderForMd/structure.md`); all other placeholders
stay empty until their phase.

```text
app/
├── models/                       # ORM — registered on Base.metadata for Alembic autogenerate
│   ├── recipe.py                 # Recipe, Ingredient, NutritionCache + Category enum, allergen/diet fields
│   └── profile.py                # Profile, Favorite, SeenHistory
├── repo/                         # ONLY layer that touches the DB (parameterized/ORM)
│   ├── recipes.py                # list_by_category(...), get_by_id(...), upsert (ingestion), eligibility filter
│   ├── profiles.py               # get/upsert profile by profile-ID
│   ├── favorites.py              # add (idempotent), list, remove, exists
│   └── seen_history.py           # insert/list — DEFINED but inert this phase (freshness is later)
├── services/
│   ├── user/
│   │   ├── constraint_guard.py   # THE WALL: violates(recipe, profile) + filter(recipes, profile); fail-closed
│   │   ├── nutrition.py          # scale cached nutrition to the cook's servings; approximate flag passthrough
│   │   └── favorites.py          # save/list/remove orchestration (guarded list)
│   └── shared/
│       └── recipe_view.py        # build RecipeCard/RecipeDetail DTOs — REQUIRES constraints → runs the guard
├── schemas/
│   ├── recipe.py                 # RecipeCard, RecipeDetail, NutritionSummary, Category (response models)
│   └── profile.py                # ProfileIn/ProfileOut (diet, allergies, servings)
├── api/
│   ├── deps.py                   # profile-ID header dependency (X-Profile-ID) + DB session dep
│   └── user/
│       ├── recipes.py            # GET /recipes?category=, GET /recipes/{id}
│       ├── favorites.py          # POST/GET/DELETE /favorites
│       └── profile.py            # GET/PUT /profile
└── infra/external/               # offline adapters (imported by ingestion, NOT by request paths)
    ├── themealdb.py              # fetch food recipes
    ├── thecocktaildb.py          # fetch non-alcoholic drinks
    └── openfoodfacts.py          # ingredient → allergens + per-100g nutrition

alembic/versions/0002_catalog.py  # recipes, ingredients, nutrition_cache, profiles, favorites, seen_history

ingestion/                        # OFFLINE pipeline (never shipped); idempotent
├── fetch_themealdb.py            # → raw food recipes
├── fetch_thecocktaildb.py        # → raw non-alcoholic drinks
├── fetch_kaggle.py               # load a RecipeNLG/Food.com subset from ingestion/data/ (gitignored)
├── categorize.py                 # map source category/tags → exactly one of the five
├── extract_ingredients.py        # parse (name, qty, unit) from raw lines
├── nutrition.py                  # aggregate OFF per-ingredient → recipe calories+macros (+approximate)
├── allergens.py                  # deterministic ingredient→allergen map (+OFF tags); certainty flag
├── load.py                       # idempotent upsert into the DB via app.repo
├── coverage.py                   # post-ingest report: per-category counts, % allergen_certain, surfaceable count per allergen
└── run_ingest.py                 # orchestrates fetch → categorize → extract → nutrition+allergens → load → coverage report

tests/
├── unit/
│   ├── test_constraint_guard.py  # the wall holds; INCL. new-output-path regression (parametrized)
│   └── test_nutrition_scaling.py # servings scaling + approximate passthrough
└── integration/
    ├── test_recipes_flow.py      # browse → detail; wall applied; verbatim steps; honest empty
    └── test_favorites.py         # save/list/remove + persistence across sessions; idempotent; guarded
```

**Structure Decision**: Single FastAPI monolith exactly as in `projectplanFolderForMd/structure.md`.
The decisive architectural choice is the **single wall choke point**: cook-facing DTOs can be produced
*only* by `recipe_view`, whose signature requires a resolved constraint profile and which internally
calls `constraint_guard`. A new endpoint that wants to show recipes must call `recipe_view`, so it
inherits the wall; the parametrized regression test enumerates every cook-facing recipe path and fails
if one returns a violating recipe.

**Fail-closed coverage visibility**: the fail-closed rule (uncertain allergen status ⇒ excluded) is a
non-negotiable safety choice, but its *cost* is corpus that allergic cooks can't see. To keep that cost
observable rather than silent, ingestion ends with a coverage report (`ingestion/coverage.py`): per
category, the `% allergen_certain` and the surfaceable recipe count for a representative allergic
profile. This makes the allergen-keyword map (the lever) measurable so we can improve recognition
without ever weakening the wall — tests still assert **zero violations**, never a minimum surfaced
count.

## Complexity Tracking

> No constitution violations; this section is intentionally empty.
