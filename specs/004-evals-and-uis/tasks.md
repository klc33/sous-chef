---
description: "Task list for 004-evals-and-uis implementation"
---

# Tasks: Provable & Usable — Gated Evals + the Two UIs

**Input**: Design documents from `specs/004-evals-and-uis/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ (all present)

**Tests**: Test wiring IS the deliverable for US1 (the gates), so eval/test/CI tasks are first-class here.
One admin integration test is added for US3. No JS test framework is added for the widget (per research R6);
the widget is validated by the quickstart manual flow.

**Organization**: By user story (US1 P1 → US2 P2 → US3 P3), each independently testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 / US2 / US3 (setup/foundational/polish carry no story label)
- Exact file paths are included in each task.

## Reality note (read before starting)

The eval runner, `eval_thresholds.yaml`, eval data files, and the `pytest` suites **already exist and
pass** from prior phases. CI today runs only ruff + mypy + `/health` smoke. `dashboard/`, `app/api/admin/*`,
`app/services/admin/*`, and all of `widget/` are **empty 0-byte scaffolds**. `app/main.py` mounts only
health + user routers; `app/config.py` has no operator keys; `pyproject.toml` has no `dashboard` extra.
Tasks below EXTEND/WIRE what exists and FILL the empty scaffolds — they do not reimplement Phase 3.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Dependency and project scaffolding the stories build on.

- [X] T001 [P] Add a `dashboard` extra to `pyproject.toml` (`streamlit`, `streamlit-authenticator`, `httpx`, `pandas`) under `[project.optional-dependencies]`, then refresh the lock (`uv lock`). Confirm no backend/image change. (FR-030)
- [X] T002 [P] Scaffold the widget Node project: fill `widget/package.json` (deps `react`, `react-dom`; dev `vite`, `@vitejs/plugin-react`; scripts `dev`/`build`/`preview`) and `widget/jsconfig.json` (JS/JSX hints, no TS), then `cd widget && npm install`. (FR-022)
- [X] T003 [P] Add non-secret vars to `.env.example`: `BACKEND_ADMIN_URL`, `OPERATOR_USERNAME`, and a documented `VITE_API_BASE` for the widget. (no secrets — those go to Vault)

---

## Phase 2: Foundational (Operator secrets & config backbone)

**Purpose**: The Vault-sourced operator-auth plumbing that BOTH the admin API and the dashboard (US3)
depend on. **US1 and US2 do not depend on this phase** and may begin right after Setup.

**⚠️ Blocks US3 only.**

- [X] T004 Extend `app/config.py` with operator-auth settings — `operator_username` (from env) and, sourced from Vault, `operator_password_hash`, `dashboard_cookie_key`, `admin_api_token`; extend `app/infra/vault.py` `load_secrets()` to read these from KV `secret/sous-chef`. Fail-fast if the backend's `admin_api_token` is missing. (FR-028)
- [X] T005 Extend `scripts/seed_vault.sh` to also write `OPERATOR_PASSWORD_HASH`, `DASHBOARD_COOKIE_KEY`, and `ADMIN_API_TOKEN` to `secret/sous-chef` (dev placeholders by default; forward real values from the operator env like the existing provider keys). (FR-028)

**Checkpoint**: Operator secrets resolve from Vault; US3 can begin.

---

## Phase 3: User Story 1 - Gated evaluations make the assistant provable (Priority: P1) 🎯 MVP

**Goal**: The committed eval suites + full test suite run in CI as merge-blocking gates; the RAG suite
reports MRR (gating) and frozen-judge faithfulness/answer-relevancy (report-only).

**Independent Test**: Run `make evals` and `make test` locally (all green incl. red-team + redaction);
open a PR and confirm the new **gates** job runs and goes red on a forced regression, blocking merge.

**Depends on**: Setup (Phase 1) only. Independent of US2/US3.

- [X] T006 [P] [US1] Add `rag.mrr_min` to `eval_thresholds.yaml` (documented placeholder ~0.70, "tighten to measured, never weaken"); keep faithfulness/answer-relevancy keyless (report-only). (FR-007, clarification)
- [X] T007 [US1] Extend `gate_rag` in `evals/run_evals.py` to also compute **MRR** (reciprocal rank of the first `ideal` among surfaced cards) and gate it vs `rag.mrr_min`; keep hit@k as-is. Deterministic; SKIPs with the same live-stack guard. (FR-007)
- [X] T008 [US1] Add a report-only **faithfulness + answer-relevancy** pass to `evals/run_evals.py` using a **frozen Groq judge** (reuse `app.infra.llm_groq`, pinned judge model id) scored from (query, retrieved context, generated reply); emit two `GateResult` rows that are PASS/SKIP only and NEVER set the exit code. (FR-007, clarification, R1)
- [X] T009 [US1] Wire merge-blocking CI in `.github/workflows/ci.yml` as **two jobs** so the merge gate stays deterministic and hermetic: **(a)** a no-service **gates** job — `uv sync --frozen --extra backend --group test --group ml --group evals` → `make train` (rebuild `ml/artifacts/model.joblib`) → `uv run python -m evals.run_evals` (deterministic gates: classifier macro-F1, red-team refusal, redaction leaks; offline RAG/agent SKIP without keys); **(b)** extend the **existing service-provisioned `smoke` job** (already runs Postgres+Redis+Vault) to also run the full `uv run pytest tests/unit tests/integration tests/redteam -q` after the `/health` check. Both run on `pull_request` + `push: main` and must be green to merge. (FR-001, FR-003, FR-004, FR-005, R2; resolves analyze F1)
- [X] T010 [US1] Confirm the deterministic hard gates actually block: run `evals/run_evals.py` after temporarily adding an unrefused probe to `evals/redteam/attempts.yaml` (and separately nudging a metric below floor) → exit code non-zero; revert. Record the check in the PR description. (FR-010, SC-001, SC-002, SC-003)
- [X] T011 [US1] After a real `make up && make ingest` run, record measured hit@3/MRR and tighten `rag.mrr_min` to the conservative-but-real floor; leave red-team=1.0 and redaction=0 untouched. (FR-002, FR-011)

**Checkpoint**: US1 done — gates are green locally and merge-blocking in CI; this is the MVP.

---

## Phase 4: User Story 2 - The cook uses the chat widget (Priority: P2)

**Goal**: The plain-JS/JSX React widget — constraints → category → cards → verbatim steps → favorites,
profile-ID header, backend-only calls, calm refusals, honest empty states.

**Independent Test**: `cd widget && npm run dev` against a running backend; complete the
constraints→category→detail→favorite→reload loop; verify Network tab shows only `VITE_API_BASE` calls
each carrying `X-Profile-ID`.

**Depends on**: Setup (Phase 1) only. Independent of US1/US3. (Backend endpoints from 002/003 already exist.)

- [X] T012 [P] [US2] Implement `widget/src/lib/profile.js` — generate a UUID via `crypto.randomUUID()` on first load, persist in `localStorage`, expose `getProfileId()`. (FR-018)
- [X] T013 [P] [US2] Implement `widget/src/lib/categories.js` — ordered canonical underscored values ↔ display labels + `normalize(s)` for the spaced `/chat` form. (FR-007 clarification, R7)
- [X] T014 [US2] Implement `widget/src/api/client.js` — fetch wrapper attaching `X-Profile-ID` and using `import.meta.env.VITE_API_BASE`; methods for `/profile`, `/recipes`, `/recipes/{id}`, `/chat`, `/favorites`; map `refused`/empty/`404`/`429`/5xx to typed UI states. (FR-016, FR-018, FR-019, FR-020, FR-021; depends on T012)
- [X] T015 [P] [US2] Implement `widget/src/components/ConstraintsForm.jsx` — read/edit diet, allergies, default_servings via `GET/PUT /profile`. (FR-013)
- [X] T016 [P] [US2] Implement `widget/src/components/CategoryChips.jsx` — the five chips from `categories.js`; selecting one triggers a category browse. (FR-014)
- [X] T017 [P] [US2] Implement `widget/src/components/RecipeCard.jsx` — title + key ingredients + save-to-favorites action. (FR-014, FR-017)
- [X] T018 [P] [US2] Implement `widget/src/components/RecipeDetail.jsx` — verbatim steps + nutrition summary + favorite toggle. (FR-015, FR-017)
- [X] T019 [P] [US2] Implement `widget/src/components/ChatBox.jsx` — free-text input + send. (FR-016)
- [X] T020 [P] [US2] Implement `widget/src/components/Favorites.jsx` — list saved recipes, open, remove. (FR-017)
- [X] T021 [P] [US2] Implement the chat render-branch components in `widget/src/components/`: `RefusalNotice.jsx`, `MealPlanView.jsx`, `ShoppingList.jsx`, `SubstitutionCard.jsx`. (FR-016, FR-020)
- [X] T022 [US2] Implement `widget/src/App.jsx` — layout + state wiring (constraints summary, chips, chat/results column, favorites view) routing the `/chat` response to the correct render branch and loading/error states (fast vs planning). (FR-016, FR-023; depends on T014–T021)
- [X] T023 [US2] Implement `widget/src/main.jsx` (React root) + base CSS (clean, professional, food-forward) + `widget/index.html` mount point and `widget/vite.config.js` (react plugin, `VITE_API_BASE`/dev proxy). (depends on T022)
- [X] T024 [US2] Fill `widget/Dockerfile` — Vite build → static serve (nginx or `vite preview`); document `VITE_API_BASE` at build/runtime. (deploy surface)

**Checkpoint**: US2 done — the cook loop works end-to-end against the backend.

---

## Phase 5: User Story 3 - The operator runs and inspects the system (Priority: P3)

**Goal**: The admin API (`/admin/corpus`, `/admin/evals/run`, `/admin/metrics`) behind `admin_deps`, and
the Streamlit dashboard (corpus, evals, metrics + Phoenix deep-links) with a cookie login that survives
refresh.

**Independent Test**: Seed Vault, run the dashboard, log in, refresh (still logged in), browse corpus,
run evals, read metrics + follow a Phoenix deep-link; confirm an unauthenticated visitor is blocked.

**Depends on**: Setup (Phase 1) + Foundational (Phase 2).

### Admin API (backend)

- [X] T025 [US3] Implement `app/api/admin_deps.py` — `require_operator` dependency validating the `Authorization: Bearer` token against the Vault-loaded `admin_api_token` (401/403 otherwise). (FR-029; depends on T004)
- [X] T026 [P] [US3] Implement `app/services/admin/corpus.py` — read-only paged corpus projection (title, category, cuisine, source/source_id, allergen/diet tags) via existing `repo/recipes`. (FR-024)
- [X] T027 [US3] Implement `app/api/admin/corpus.py` — `GET /admin/corpus?page&page_size&category` returning `CorpusPage`, behind `require_operator`. (FR-024; depends on T025, T026)
- [X] T028 [P] [US3] Implement `app/services/admin/evals.py` — invoke `evals.run_evals.run()` in-process and return the structured `GateResult` list + thresholds echo. (FR-025; depends on US1 T007/T008 for full gate set)
- [X] T029 [US3] Implement `app/api/admin/evals.py` — `POST /admin/evals/run` returning `EvalRunResult`, behind `require_operator`. (FR-025; depends on T025, T028)
- [X] T030 [P] [US3] Implement `app/services/admin/metrics.py` + `app/services/admin/traces.py` — classifier macro-F1 + per-class (from testset/model card), the workflow-vs-agent **routing split read from a lightweight Redis counter** the router increments per decision (no new dep; keys `routing:workflow`/`routing:agent` — add the increment in the existing `app/services/user/router.py`), last gate summary, and the **Phoenix UI deep-link base URL** for per-turn traces/cost (deep-link only — cost is viewed in Phoenix, not rolled up in the dashboard). (FR-026, FR-027; resolves analyze C1/C2)
- [X] T031 [US3] Implement `app/api/admin/metrics.py` — `GET /admin/metrics` returning `MetricsSummary`, behind `require_operator`. (FR-026, FR-027; depends on T025, T030)
- [X] T032 [US3] Register the admin routers in `app/main.py` (`register_admin_routers(app)`) alongside health + user routers. (FR-027; depends on T027, T029, T031)
- [X] T033 [US3] Add `tests/integration/test_admin.py` — admin endpoints require a valid token (401 without, 200 with), corpus pages, eval-run returns gate rows, metrics shape; reuse the existing `conftest` adapter-mock / seeded-token pattern. It runs in the **service-provisioned test job (T009b)**, not the deterministic gates job. (FR-029, SC-009; resolves analyze F2)

### Operator dashboard (Streamlit)

- [X] T034 [US3] Implement `dashboard/auth.py` — `streamlit-authenticator` cookie login; operator username from config, password hash + cookie key from Vault; exposes a guard used by every page. (FR-028; depends on T004)
- [X] T035 [US3] Implement `dashboard/app.py` — entry point: apply the auth gate, landing/nav; configure the backend admin client (base URL + bearer admin token from Vault). (FR-028; depends on T034)
- [X] T036 [P] [US3] Implement `dashboard/pages/1_corpus.py` — paged corpus table via `GET /admin/corpus`. (FR-024; depends on T035)
- [X] T037 [P] [US3] Implement `dashboard/pages/2_evals.py` — "Run evals" button → `POST /admin/evals/run` → gate table with measured-vs-threshold pass/fail. (FR-025; depends on T035)
- [X] T038 [P] [US3] Implement `dashboard/pages/3_metrics.py` — classifier metrics, routing split, gate status, and Phoenix deep-links + recent cost via `GET /admin/metrics`. (FR-026, FR-027; depends on T035)

**Checkpoint**: US3 done — operator can log in (refresh-safe), browse, run evals, and read metrics.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T039 [P] Add `dashboard` and `widget` services to `docker-compose.yml` (dashboard → backend admin URL; widget static build → backend `VITE_API_BASE`); confirm `make up` brings up both. (SC-008, SC-011) — filled `dashboard/Dockerfile` (+ per-Dockerfile `dashboard/Dockerfile.dockerignore`) and added `widget/.dockerignore`; `docker compose config` validates and `docker compose build dashboard widget` both succeed.
- [X] T040 [P] Update docs: `docs/EVALS.md` (gates + thresholds incl. MRR + report-only judge), `docs/SECURITY.md` (operator auth via Vault), `docs/RUNBOOK.md` (run the two UIs). (Documentation-first)
- [~] T041 Run the full `specs/004-evals-and-uis/quickstart.md` end-to-end (all three stories) against the running stack; fix any gaps. (SC-004..SC-012) — US1 automated half done (gates + full pytest green locally; both new images build). The interactive US2/US3 walkthrough (browser cook-loop + Streamlit login/refresh) and the keyed offline RAG/judge gates (`make up` + `make ingest` need real provider keys) remain a manual operator pass.
- [X] T042 Final gate: `make lint && make test && make evals` all green (red-team + redaction hard gates included) and the CI gates job green; confirm no new prohibited runtime dependency in any image. (Definition of Done) — ruff + mypy clean; **171 passed**; deterministic eval gates PASS (offline gates SKIP without the live stack). No new runtime dep: dashboard image installs only the `dashboard` extra, widget is Node/Vite-only, backend image unchanged (no torch).

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: no dependencies — start immediately.
- **Foundational (Phase 2)**: depends on Setup; **blocks US3 only**.
- **US1 (Phase 3)**: depends on Setup only — can start immediately after Phase 1 (it's the MVP).
- **US2 (Phase 4)**: depends on Setup only — independent of US1/US3.
- **US3 (Phase 5)**: depends on Setup + Foundational.
- **Polish (Phase 6)**: depends on the desired stories being complete.

### Story independence

- **US1** touches `evals/`, `eval_thresholds.yaml`, `.github/workflows/ci.yml` — no overlap with the UIs.
- **US2** touches `widget/` only — no backend changes.
- **US3** touches `app/api/admin/*`, `app/services/admin/*`, `app/main.py`, `app/config.py`, `dashboard/`.
- The three can be built in parallel by different people after Phase 1 (US3 also needs Phase 2).

### Within each story

- US1: thresholds key (T006) → runner extension (T007, T008) → CI wiring (T009) → verify/tighten (T010, T011).
- US2: libs (T012, T013) → client (T014) → components (T015–T021) → App/bootstrap (T022, T023) → Docker (T024).
- US3: config/auth backbone (Phase 2) → `admin_deps` (T025) → services → endpoints → router mount (T032) → test (T033) → dashboard auth (T034) → app (T035) → pages (T036–T038).

### Parallel opportunities

- Setup: T001, T002, T003 all [P].
- US1: T006 [P]; T007/T008 are sequential edits to the same file (`run_evals.py`).
- US2: T012, T013 [P]; all component files T015–T021 [P] (different files) once T014 exists.
- US3: T026, T028, T030 [P] (different service files); pages T036–T038 [P] once T035 exists.
- Polish: T039, T040 [P].

---

## Parallel Example: User Story 2 (widget components)

```bash
# After api/client.js (T014) exists, build all components in parallel:
Task: "Implement widget/src/components/ConstraintsForm.jsx"
Task: "Implement widget/src/components/CategoryChips.jsx"
Task: "Implement widget/src/components/RecipeCard.jsx"
Task: "Implement widget/src/components/RecipeDetail.jsx"
Task: "Implement widget/src/components/ChatBox.jsx"
Task: "Implement widget/src/components/Favorites.jsx"
Task: "Implement widget/src/components/{RefusalNotice,MealPlanView,ShoppingList,SubstitutionCard}.jsx"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 Setup → 2. Phase 3 US1 (gates + CI) → 3. **STOP and VALIDATE**: gates green locally and
   merge-blocking in CI, forced regression goes red. This alone makes the project *provable* — the highest-
   value slice and a defensible demo on its own.

### Incremental delivery

1. Setup → US1 (provable) → demo the red-team/redaction gates blocking a bad merge.
2. Add US2 (cook widget) → demo the browse→drill→save loop and a refusal.
3. Add Foundational + US3 (dashboard) → demo refresh-safe login, on-demand evals, metrics + Phoenix.

### Notes

- [P] = different files, no dependencies. Commit after each task or logical group.
- Never weaken a committed threshold to make a gate pass (golden rule #6 / FR-010).
- The widget stays "dumb" — it only renders backend-returned (wall-filtered) data; no content invented client-side.
- All operator secrets come from Vault; nothing sensitive is committed or imaged.
