---
description: "Task list for 002-catalog-wall-favorites implementation"
---

# Tasks: Recipe Catalog, the Safety Wall & Favorites

**Input**: Design documents from `specs/002-catalog-wall-favorites/`

**Prerequisites**: [plan.md](plan.md) (required), [spec.md](spec.md) (user stories), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/)

**Tests**: Included — the spec explicitly requires unit-testing the constraint guard (incl. a "new output path forgets the guard" regression) and favorites persistence. Test scope is targeted, not full TDD.

**Organization**: Tasks are grouped by user story (US1–US3, priority order). The monolith tree already exists as empty placeholders (`projectplanFolderForMd/structure.md`); tasks **fill** files. Per the repo rule, **every function gets a comment** explaining how it works.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US3; Setup/Foundational/Polish carry no story label
- Exact repo-relative file paths are given in each task

## Path Conventions

Single FastAPI monolith at repo root: `app/`, `alembic/`, `ingestion/`, `tests/`. Paths are repo-relative.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Offline dependency group and data placement — no new runtime deps (the surface is plain Python + SQL).

- [X] T001 Add an offline `ingestion` dependency group with `pandas` via `uv add --group ingestion pandas`, then re-lock (`uv lock`). Confirm the `backend` extra already provides sqlalchemy/alembic/psycopg/pydantic — **no new runtime deps**, no torch.
- [X] T002 [P] Add `ingestion/data/README.md` documenting the manually-downloaded Kaggle subset (RecipeNLG/Food.com) expected under `ingestion/data/` (gitignored), and confirm `.gitignore` covers `ingestion/data/` and `ingestion/cache/`.

---

## Phase 2: Foundational (Blocking Prerequisites — "the data layer" + the shared wall)

**Purpose**: Schema, repositories, the deterministic wall, the DTO choke point, and the ingestion pipeline that produces the corpus. Every user story depends on this phase.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### Schema & migration

- [X] T003 Define domain enums (`Category`, `Allergen`, `Diet`, `Source` as `StrEnum`) and ORM models `Recipe`, `Ingredient`, `NutritionCache` in `app/models/recipe.py` per [data-model.md](data-model.md) (allergens/diet flags, `allergen_certain`, `is_complete`, `UNIQUE(source, source_id)`).
- [X] T004 [P] Define ORM models `Profile`, `Favorite` (composite PK `profile_id, recipe_id`), `SeenHistory` in `app/models/profile.py`.
- [X] T005 Register the new models in `app/models/__init__.py` so Alembic autogenerate targets them (depends T003, T004).
- [X] T006 Create migration `alembic/versions/0002_catalog.py` for all six tables with indexes/constraints from [data-model.md](data-model.md) (`INDEX(category, is_complete)`, favorites composite PK, FKs `ON DELETE CASCADE`); generate via autogenerate, then review by hand (depends T005).

### Response/request schemas

- [X] T007 [P] Add Pydantic models in `app/schemas/recipe.py` (`Category`, `RecipeCard`, `NutritionSummary`, `RecipeDetail`) and `app/schemas/profile.py` (`ProfileIn`, `ProfileOut`) matching [contracts/](contracts/).

### Repositories (the ONLY DB access layer)

- [X] T008 [P] Implement `app/repo/recipes.py`: `upsert_recipe(...)` idempotent on `(source, source_id)` (recipe + ingredients + nutrition in one transaction), `list_by_category(session, category)` filtering `is_complete = true`, `get_by_id(session, id)` eager-loading ingredients + nutrition. ORM/parameterized only (depends T003).
- [X] T009 [P] Implement `app/repo/profiles.py`: `get(session, profile_id)` → row or None, `upsert(session, profile_id, diet, allergies, servings)` (depends T004).
- [X] T010 [P] Implement `app/repo/favorites.py`: `add(...)` idempotent, `list(session, profile_id)`, `remove(...)`, `exists(...)` (depends T004).
- [X] T011 [P] Implement `app/repo/seen_history.py`: `insert(...)`, `list(...)` — DEFINED but unused this phase (freshness is later); include a module docstring saying so (depends T004).

### Request dependencies

- [X] T012 [P] Implement `app/api/deps.py`: `require_profile_id` (reads `X-Profile-ID`, 400 if missing/blank; owner never from body) and a `get_db` session dependency sourced from `app.state.db`.

### The wall + the DTO choke point

- [X] T013 Implement `app/services/user/constraint_guard.py`: `ConstraintProfile` value object (with `default()` and `from_row(profile)` factories), `violates(recipe, cp)` per the [data-model.md](data-model.md) predicate (allergen intersection; **fail closed** when `allergen_certain` is false and the cook has allergies; diet flags; `diet=none` never filters), and `filter(recipes, cp)`. Pure, deterministic, fully commented (depends T003).
- [X] T014 Implement `app/services/shared/recipe_view.py` as the single choke point: `to_cards(recipes, cp)` and `to_detail(recipe, cp, *, is_favorite, nutrition)` that **require** a `ConstraintProfile` and run `constraint_guard.filter`/`violates` before building any DTO — so no output path can produce a card/detail without the wall. `RecipeCard.key_ingredients` = the recipe's first up-to-four parsed ingredients in stored order (FR-011) (depends T007, T013).

### Offline source adapters (`infra/external` — imported ONLY by ingestion)

- [X] T015 [P] Implement `app/infra/external/themealdb.py`: list/lookup food recipes (ingredient+measure pairs, instructions) via httpx.
- [X] T016 [P] Implement `app/infra/external/thecocktaildb.py`: fetch non-alcoholic drinks (filter `strAlcoholic = "Non alcoholic"`).
- [X] T017 [P] Implement `app/infra/external/openfoodfacts.py`: ingredient → allergen tags + per-100g nutriments, with a simple on-disk cache under `ingestion/cache/`.

### Ingestion pipeline (offline, idempotent)

- [X] T018 [P] Implement `ingestion/fetch_themealdb.py`, `ingestion/fetch_thecocktaildb.py`, `ingestion/fetch_kaggle.py` (Kaggle reads the subset CSV from `ingestion/data/`) → normalized raw-recipe dicts (depends T015, T016).
- [X] T019 Implement `ingestion/categorize.py`: map source category/tags → exactly one of five (drinks: hot vs cold by keyword cues; food: curated lookup; ambiguous → documented default) per [research.md](research.md) §2.
- [X] T020 Implement `ingestion/extract_ingredients.py`: parse `(name, quantity, unit)` from source/raw lines (regex + units whitelist), always retaining `raw_text` (research §3).
- [X] T021 Implement `ingestion/allergens.py`: curated allergen→keyword map + OFF tags → `recipes.allergens` + `allergen_certain`; derive `is_vegetarian/vegan/pescatarian` (research §4–5) (depends T017).
- [X] T022 Implement `ingestion/nutrition.py`: aggregate OFF per-ingredient nutriments → calories + protein/carbs/fat for `basis_servings`; set `is_approximate` + `unmapped_ingredient_count` (research §6) (depends T017).
- [X] T023 Implement `ingestion/load.py`: compute `is_complete`, then idempotent upsert via `app.repo.recipes.upsert_recipe` (recipe + ingredients + nutrition in one txn) (depends T008, T019–T022).
- [X] T024 [P] Implement `ingestion/coverage.py`: post-ingest report — per-category counts, `% allergen_certain`, and surfaceable count for a representative allergic profile (the fail-closed visibility lever).
- [X] T025 Implement `ingestion/run_ingest.py`: orchestrate fetch → categorize → extract → allergens + nutrition → load → coverage; idempotent and re-runnable (depends T018–T024).
- [X] T026 [P] Add a `make ingest` target → `uv run python -m ingestion.run_ingest` in the `Makefile`.

**Checkpoint**: `alembic upgrade head` applies `0002_catalog`; `make ingest` loads a deduplicated corpus where every surfaceable recipe has category + ingredients + allergens + nutrition; the wall and `recipe_view` choke point compile. User stories can begin.

---

## Phase 3: User Story 1 — Browse safe recipes by category (Priority: P1) 🎯 MVP

**Goal**: A cook sets diet/allergies/servings once, picks one of five categories, and sees only real, constraint-compliant recipe cards.

**Independent Test**: Set a nut allergy, browse each category → every card is a real corpus recipe and none contains nuts; no constraints → all category recipes shown; a category with no compliant recipe → honest `[]`.

### Tests for User Story 1

- [X] T027 [P] [US1] Unit-test the wall in `tests/unit/test_constraint_guard.py`: `violates` for an allergen hit, **fail-closed** on `allergen_certain = false`, each diet (vegan/vegetarian/pescatarian) and `diet=none`; `filter` drops violators and keeps compliant.
- [X] T028 [US1] Integration test `tests/integration/test_recipes_flow.py`: seed compliant + violating recipes across multiple categories; nut-allergic profile via `X-Profile-ID` browses → only compliant cards (title + key ingredients) AND **every returned card's `category` equals the requested category** (category purity, SC-005); no-constraint profile sees all; empty-compliant category returns `[]`.

### Implementation for User Story 1

- [X] T029 [US1] Implement `app/api/user/profile.py`: `GET /profile` (returns defaults — `none`/no allergies/servings 2 — when unset) and `PUT /profile` (validate diet/allergens/servings, upsert) via `repo.profiles` + `deps.require_profile_id` (depends T009, T012).
- [X] T030 [US1] Implement `app/api/user/recipes.py`: `GET /recipes?category=` → resolve `ConstraintProfile` from `repo.profiles.get` (or default) → `repo.recipes.list_by_category` → `recipe_view.to_cards(cp)` → 200 list (depends T008, T013, T014, T012).
- [X] T031 [US1] Create `app/api/user/__init__.py` with `register_user_routers(app)` (profile + recipes routers) and call it from `create_app` in `app/main.py` (depends T029, T030).

**Checkpoint**: A cook can set constraints and browse safe, real cards in each category — the safe MVP. Deployable.

---

## Phase 4: User Story 2 — Open a recipe for full instructions and nutrition (Priority: P2)

**Goal**: Clicking a card shows the recipe's stored steps verbatim plus nutrition scaled to the cook's servings; the detail path cannot bypass the wall.

**Independent Test**: Open a card from US1 → steps match stored steps verbatim and nutrition is scaled to servings (`is_approximate` when partial); request a violating recipe by id → 404.

### Tests for User Story 2

- [X] T032 [P] [US2] Unit-test `tests/unit/test_nutrition_scaling.py`: scaling from `basis_servings` to cook servings and `is_approximate` passthrough.
- [X] T033 [US2] Extend `tests/integration/test_recipes_flow.py`: open a card → verbatim steps + scaled nutrition; a recipe violating the profile → `GET /recipes/{id}` returns 404 (no bypass, no existence leak).

### Implementation for User Story 2

- [X] T034 [P] [US2] Implement `app/services/user/nutrition.py`: `scale(nutrition_cache_row, cook_servings)` → `NutritionSummary` (calories + macros), preserving `is_approximate` (depends T007).
- [X] T035 [US2] Add `GET /recipes/{id}` to `app/api/user/recipes.py`: `repo.recipes.get_by_id`; if missing OR `violates(cp)` → 404; else `recipe_view.to_detail(cp, is_favorite=…, nutrition=scaled)` with verbatim steps (depends T013, T014, T034, T010).

**Checkpoint**: US1 + US2 work — browse and cook a recipe, grounded and safe.

---

## Phase 5: User Story 3 — Save and revisit favorites (Priority: P3)

**Goal**: Save/list/open/remove favorites, persisted per passwordless profile across sessions; the list is wall-filtered and saves are idempotent.

**Independent Test**: Save a recipe, reload/new session with the same profile-ID → still listed; remove → gone; double-save → one entry; add a violating allergy → favorite no longer surfaced.

### Tests for User Story 3

- [X] T036 [US3] Integration test `tests/integration/test_favorites.py`: save/list/remove; idempotent double-save; persistence across a fresh client with the same `X-Profile-ID`; after adding a violating allergy via `PUT /profile`, the favorite is omitted from `GET /favorites` (wall on the list).

### Implementation for User Story 3

- [X] T037 [US3] Implement `app/services/user/favorites.py`: `save` (idempotent via `repo.favorites.add`), `list` (resolve `cp`, fetch saved recipes, run `constraint_guard.filter`, build cards via `recipe_view.to_cards`), `remove` (depends T010, T013, T014).
- [X] T038 [US3] Implement `app/api/user/favorites.py`: `POST` (404 if recipe_id unknown, else 201 idempotent), `GET` (guarded cards), `DELETE /{recipe_id}` (204 idempotent); register the router in `app/api/user/__init__.py` (depends T037, T031).

**Checkpoint**: All three stories are independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: The architectural safety regression and end-to-end validation across all stories.

- [X] T039 [P] Add the **"new output path forgets the guard" regression** to `tests/integration/test_wall_regression.py` (drives the real DB-backed HTTP endpoints, so it lives with the integration suite rather than the pure-unit `test_constraint_guard.py`): a parametrized test over EVERY cook-facing recipe path (`GET /recipes`, `GET /recipes/{id}`, `GET /favorites`) feeding a nut-allergic profile and asserting **0** violating recipes — adding a path that skips `recipe_view` fails this test.
- [X] T040 [P] Run `make lint` (ruff + mypy) and fix; verify **every new function has an explanatory comment** (repo rule).
- [X] T041 Run the [quickstart.md](quickstart.md) scenarios A–C against `make up`, plus the corpus-sanity SQL (0 rows) and the SC-001 check (nut-allergic cook sees 0 nut recipes anywhere).
- [X] T042 [P] Update `docs/RUNBOOK.md` with the ingestion run + Kaggle-subset placement, and confirm `make test` runs the new unit + integration suites.

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (P1)**: no dependencies.
- **Foundational (P2)**: depends on Setup — BLOCKS all user stories. Within it: schema (T003–T006) → repos (T008–T011) + schemas (T007) + deps (T012) + wall/view (T013–T014); ingestion (T015–T026) depends on repos but is otherwise parallel to the wall/view work.
- **US1 (P3)**: after Foundational. The MVP.
- **US2 (P4)**: after US1 (extends `recipe_view` and the recipes router; needs cards to open).
- **US3 (P5)**: after Foundational; reuses the wall/view from Foundational + US1's router registration.
- **Polish (P6)**: after US1–US3 (the regression parametrizes over all three paths).

### Within each user story

- Tests alongside implementation; models → repos → services → endpoints; story complete before next priority.

### Parallel opportunities

- Setup: T002 ‖ T001.
- Foundational: T004 ‖ T003; T007/T008/T009/T010/T011/T012 ‖ once their model deps land; infra/external T015 ‖ T016 ‖ T017; coverage T024 ‖ ingestion stages.
- US tests marked [P] run alongside their implementation files.

---

## Parallel Example: Foundational repositories

```bash
# After models (T003, T004) land, these touch different files:
Task: "Implement app/repo/recipes.py (T008)"
Task: "Implement app/repo/profiles.py (T009)"
Task: "Implement app/repo/favorites.py (T010)"
Task: "Implement app/repo/seen_history.py (T011)"
Task: "Implement app/api/deps.py (T012)"
```

---

## Implementation Strategy

### MVP first (US1 only)

1. Setup → 2. Foundational (schema + repos + wall + view + **ingestion corpus**) → 3. US1 → **STOP & VALIDATE**: a nut-allergic cook browses every category and sees only real, nut-free cards. Deploy/demo.

### Incremental delivery

US1 (browse, MVP) → US2 (detail + nutrition) → US3 (favorites). Each adds value without breaking the prior; the wall holds on every new path (enforced by T039).

---

## Notes

- The wall is the grade: every recipe leaves through `recipe_view`, which requires a `ConstraintProfile`; T039 fails if any path skips it.
- Ground everything: steps render verbatim from `recipes.steps`; no safe match → honest empty/404.
- `seen_history` is built but inert this phase (freshness is a later feature).
- No runtime external calls: nutrition/allergens are precomputed at ingestion; request paths are plain SQL + Python.
- Commit after each task or logical group; verify each story independently at its checkpoint.
