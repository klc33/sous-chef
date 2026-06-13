---

description: "Task list for 006-corpus-data-quality"
---

# Tasks: Corpus Data Quality — Honest Nutrition & Images

**Input**: Design documents from `/specs/006-corpus-data-quality/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/recipe-surface.md](./contracts/recipe-surface.md)

**Tests**: Included — the spec's acceptance bar and the constitution's Definition of Done explicitly
require fallback/coverage tests plus the unchanged wall/grounding/redaction/red-team gates.

**Widget test strategy (no new dependency)**: the widget has **no JS unit-test runner** (Vite only), and
adding one would breach plan.md's "no new dependency" + constitution X. So the image helper's *logic* is
kept pure and its automated guard is a **dependency-free Python test** that every `Category` value has a
committed placeholder asset (the guarantee that resolution can never fail). The actual `imageFor`
selection + `<img>`/`onError` *rendering* is validated manually in [quickstart.md](./quickstart.md) §3.

**Organization**: Tasks are grouped by user story. **Important state note:** the nutrition half (US1) and
the backfill/data-source half (US3) are **already implemented** in the tree; their tasks are therefore
**verification-against-this-spec** tasks (a real action: confirm behaviour matches the now-formal
contracts, or fix any drift). The **image** half (US2) is genuine net-new build work and is the true
deliverable increment of this feature.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 / US2 / US3 (maps to spec.md user stories)

## Path Conventions

Web-app monolith + widget (per plan.md): backend in `app/`, ingestion in `ingestion/`, scripts in
`scripts/`, widget in `widget/src/`, docs in `docs/`, tests in `tests/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization for the net-new (image) work.

- [X] T001 Create the committed placeholder asset directory `widget/src/assets/placeholders/`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Confirm the shared persisted + DTO surface every story relies on — establishes that **no
migration** is needed before any story proceeds.

**⚠️ CRITICAL**: Verify these before story work.

- [X] T002 [P] Verify the persisted schema already supports the feature in `app/models/recipe.py` (and migrations): `recipes.image_url` is nullable and `nutrition_cache` carries `is_approximate` + `unmapped_ingredient_count`; confirm NO new Alembic migration is required
- [X] T003 [P] Verify `app/schemas/recipe.py` `NutritionSummary` exposes `unmapped_ingredient_count` (≥0, default 0) and that `contracts/recipes.openapi.yaml` matches it and the nullable `image_url` (no change expected)

**Checkpoint**: Schema/DTO confirmed stable — stories can proceed.

---

## Phase 3: User Story 1 - Honest nutrition on every recipe (Priority: P1) 🎯 MVP

**Goal**: Every recipe shows complete macros, an honest "estimated from N of M ingredients" partial
note, or — only when nothing is measurable — "not available"; never an invented number. Food.com-sourced
recipes show exact macros.

**Independent Test**: Open one recipe per state (complete / partial / absent) and confirm the rendered
copy matches contract C1; confirm a Food.com recipe is exact (no "(approximate)").

**Status**: implemented — these tasks verify the existing code against the formalized spec/contract and
fix any drift.

### Tests for User Story 1

- [X] T004 [P] [US1] Confirm `tests/unit/test_nutrition_fallback.py` covers contract C1 fallback behaviours (curated tables, `_grams` count/unit-less resolution, `aggregate` OFF→USDA fallback, always-`is_approximate`) and passes: `uv run pytest tests/unit/test_nutrition_fallback.py -q`
- [X] T005 [P] [US1] Confirm `tests/unit/test_nutrition_scaling.py` asserts `scale()` carries `is_approximate` + `unmapped_ingredient_count` verbatim and passes: `uv run pytest tests/unit/test_nutrition_scaling.py -q`

### Implementation for User Story 1

- [X] T006 [US1] Verify `ingestion/nutrition.py` `from_food_com` converts Food.com PDV→grams via the fixed FDA DV constants and stores `is_approximate = false` (research R1, FR-006), and that `aggregate` is unconditionally `is_approximate = true` (FR-005)
- [X] T007 [US1] Verify `ingestion/ingredient_nutrition_data.py` reflects the frequency-driven curated set and deliberately omits variable count units (can/package/stick/piece) so they stay unmapped (research R2, FR-004); add any high-frequency missing staple surfaced by the coverage report
- [X] T008 [US1] Verify the three nutrition states render in `widget/src/components/RecipeDetail.jsx` per contract C1 (complete; partial "estimated from N of M ingredients" where N = ingredient count − `unmapped_ingredient_count`; absent only when all macros are 0)
- [X] T009 [US1] Verify the chat `nutrition_q` reply in `app/services/user/workflow.py` mirrors the partial-coverage "Estimated from N of M ingredients" note (FR-011)

**Checkpoint**: Nutrition honesty is provably correct and matches the spec.

---

## Phase 4: User Story 2 - Every recipe shows a truthful image (Priority: P1)

**Goal**: Every card and detail renders an image — the real source photo when present, otherwise a
generic per-category placeholder; a failed load falls back to the placeholder; `alt` names the recipe;
never a third-party photo presented as the dish.

**Independent Test**: Browse/open a recipe with a source image (real photo), one without (placeholder),
and one with a broken URL (falls back to placeholder); confirm `alt` = title throughout.

**Status**: net-new build — this is the feature's primary deliverable.

### Tests for User Story 2

- [X] T010 [US2] Add `tests/unit/test_image_placeholders.py` asserting that for **every** `Category` value in `app/models/recipe.py` a committed placeholder asset `widget/src/assets/placeholders/{value}.svg` exists — the dependency-free guarantee that a recipe without `image_url` can always resolve to a category placeholder (FR-013/016, SC-004). Depends on T011. (The `imageFor` selection + `onError` render path is verified manually per quickstart §3 — no JS test runner is added.)

### Implementation for User Story 2

- [X] T011 [P] [US2] Create five generic per-category placeholder SVGs in `widget/src/assets/placeholders/`: `hot_drink.svg`, `cold_drink.svg`, `breakfast.svg`, `lunch.svg`, `dinner.svg` — clearly generic, never a specific dish (grounding, FR-016)
- [X] T012 [US2] Create `widget/src/lib/images.js` exporting `imageFor(recipe) → { src, alt }`: `src` = `recipe.image_url` else the placeholder for `recipe.category` (keyed on the canonical value from `lib/categories.js`); `alt` = `recipe.title` (depends on T011)
- [X] T013 [US2] Update `widget/src/components/RecipeCard.jsx` to render `<img alt={title}>` via `imageFor` with an `onError` handler that swaps to the category placeholder — replacing today's blank `card__img--placeholder` div + `alt=""` (FR-013/014/015, depends on T012)
- [X] T014 [US2] Update `widget/src/components/RecipeDetail.jsx` to render the recipe image via `imageFor` with the same `onError` placeholder fallback (today it renders no image) (FR-013/014/015, depends on T012)
- [X] T015 [P] [US2] Add consistent detail-image sizing + `object-fit` and placeholder styling in `widget/src/styles.css`
- [X] T016 [US2] Record the image-grounding decision in `docs/DECISIONS.md` (generic per-category placeholder over a borrowed/stock/AI photo = grounding; client-side, no image service, no schema change)

**Checkpoint**: No surface shows a broken or misrepresenting image.

---

## Phase 5: User Story 3 - Operator raises coverage reproducibly (Priority: P2)

**Goal**: The operator swaps in Food.com, runs a clean ingest and/or the offline backfill, and watches
the "no nutrition" rate drop — reproducibly, without downgrading exact rows or touching unrelated fields.

**Independent Test**: Run the backfill twice from a clean state; confirm idempotency, the before/after
report, and that authoritative rows are skipped.

**Status**: implemented — verify against contract C3 and confirm docs.

### Implementation for User Story 3

- [X] T017 [US3] Verify `scripts/backfill_nutrition.py` against contract C3: offline (on-disk OFF cache + new fallback, no live calls), idempotent, skips `is_approximate = false` rows, writes only the `nutrition_cache` row via `app/repo/recipes.py` helpers (`iter_with_nutrition`, `set_nutrition`), and prints a before/after all-zero report
- [X] T018 [P] [US3] Add `tests/unit/test_foodcom_parsing.py` asserting a representative Food.com ingredient line parses into structured `quantity` + `unit` + `name` (so it supports serving-scaling and per-ingredient aggregation) via `ingestion/extract_ingredients` (FR-009)
- [X] T019 [P] [US3] Verify `ingestion/data/README.md` states Food.com RAW_recipes is preferred over RecipeNLG (per-line quantities + authoritative per-serving nutrition; RecipeNLG is names-only and nutrition-uncomputable) (research R5, FR-008)
- [X] T020 [P] [US3] Verify `docs/RUNBOOK.md` documents running the nutrition backfill and refreshing the corpus from Food.com (`make ingest`)
- [X] T021 [US3] On the running stack, run `scripts/backfill_nutrition.py` twice and confirm the second run leaves the after-zero count unchanged and reports authoritative rows as skipped (quickstart §4)

**Checkpoint**: Coverage improvement is reproducible and safe.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T022 Run `ingestion` coverage reporting on a Food.com subset before vs. after and record **both** the nutrition coverage change (usable-nutrition rise + "not available" drop) **and** the image coverage change (share with a source `image_url` vs. placeholder) (SC-001, spec US3 acceptance #4, quickstart §5)
- [X] T023 [P] Cross-check docs are internally consistent and reference the 006 plan (CLAUDE.md SpecKit marker already repointed)
- [X] T024 Run `make lint && make test && make evals`; confirm the wall / grounding / redaction / red-team gates are green and behaviourally unchanged (Definition of Done, SC-006, FR-020)
- [X] T025 Execute `quickstart.md` end-to-end (all six sections) on the running stack
- [X] T026 Wire the deferred **Presidio PII redaction** into `app/core/redaction.py`: use `presidio-analyzer` + `presidio-anonymizer` (already in the `backend` extra) so `redact(text)` masks PII (email, phone, person, location, credit-card, etc.) in addition to today's secret/token patterns — closing the gap left when Foundation deferred this to "Phase 3" (003) and 003 shipped against the stub instead (`redaction.py` docstring + `001-foundation/research.md:44`). Keep the existing deterministic secret/token masking and the `redact`/`redact_mapping` signatures; the rail (`app/guardrails/output_rails.py`) and the tracing/log processors call sites are unchanged. Extend `tests/unit/test_redaction.py` to assert a representative email and phone never survive in cleartext (the redaction gate stays `leak_count_max: 0`). Then correct the now-stale claims that Presidio is "already wired" — the `redaction.py` docstring, `specs/003-intelligent-behavior/plan.md:43` ("already wired for redaction") and `research.md:204` ("existing Presidio redaction"). Verify live per the quickstart redaction check and `make evals`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational (Phase 2)**: after Setup; confirms shared schema/DTO — verification only, blocks nothing destructive.
- **User Stories (Phase 3–5)**: after Foundational. US1 and US3 are verification of existing code and are independent of US2. US2 is independent net-new build.
- **Polish (Phase 6)**: after the stories you intend to ship.

### User Story Dependencies

- **US1 (P1)** — independent (verification of the nutrition pipeline).
- **US2 (P1)** — independent (image build); only internal dependency chain T011 → T012 → {T013, T014}.
- **US3 (P2)** — independent (backfill + docs verification); relies on the same nutrition logic US1 verifies but does not block on US2.

### Within User Story 2 (the build)

- T011 (assets) → {T012 (helper), T010 (placeholder-coverage test)} → T013 + T014 (consumers, parallel to each other). T015 (CSS) is [P] and independent.

### Parallel Opportunities

- T002, T003 (Foundational) in parallel.
- US1 tests T004, T005 in parallel.
- US2: T011 (assets) and T015 (CSS) in parallel; then T010 (test) and T012 (helper) in parallel once assets exist; T013/T014 in parallel once T012 lands.
- US3: T018 (parse test), T019, T020 (doc verifications) in parallel.

---

## Parallel Example: User Story 2

```bash
# Kick off the independent pieces together:
Task: "Create 5 per-category placeholder SVGs in widget/src/assets/placeholders/"  # T011
Task: "Add detail-image sizing in widget/src/styles.css"                   # T015
# Then, after the assets (T011) land:
Task: "Add placeholder-coverage test in tests/unit/test_image_placeholders.py"  # T010
Task: "Create the imageFor helper in widget/src/lib/images.js"             # T012
# Then, after the helper (T012) lands:
Task: "Wire imageFor into RecipeCard.jsx"                                  # T013
Task: "Wire imageFor into RecipeDetail.jsx"                                # T014
```

---

## Implementation Strategy

### MVP scope

Spec marks **US1 and US2 both P1**. US1 (honest nutrition) is **already satisfied** in the tree, so the
first net-new shippable increment is **US2 (truthful images)** — Phases 1, 2, 4. Ship that, then verify
US1 (Phase 3) and US3 (Phase 5), then Polish (Phase 6).

### Incremental delivery

1. Setup + Foundational → schema confirmed, asset dir ready.
2. **US2 images** → test independently (real photo / placeholder / broken-URL fallback) → demo.
3. **US1 nutrition** → verify the three states + exactness against the spec.
4. **US3 backfill/coverage** → prove reproducible coverage rise.
5. **Polish** → full `make lint && make test && make evals` + quickstart.

---

## Notes

- [P] = different files, no incomplete-task dependency.
- US1/US3 tasks are verification-against-spec because the code already exists; treat a mismatch as a bug
  to fix, not a no-op.
- Never weaken a gate to pass CI (golden rule #6); the wall and grounding stay deterministic and
  untouched (FR-020).
- T026 is net-new security work (not a 006 corpus-data concern) folded into Polish because it was
  surfaced here: live testing showed the output rail redacts secrets/tokens but not PII, since the
  Presidio wiring Foundation deferred to "Phase 3" was never actually done. Tracked here so it stops
  living only in a stale docstring.
- Commit after each task or logical group.
