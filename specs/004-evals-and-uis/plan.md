# Implementation Plan: Provable & Usable — Gated Evals + the Two UIs

**Branch**: `004-evals-and-uis` | **Date**: 2026-06-11 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/004-evals-and-uis/spec.md`

## Summary

Make Sous-Chef **provable** and **usable** without adding business logic. Two tracks:

1. **Provable** — turn the already-implemented eval suites into *merge-blocking* CI gates and complete the
   RAG suite. Today `evals/run_evals.py` scores classifier macro-F1, red-team refusal, redaction leaks
   (deterministic) plus RAG hit@3 and agent tool-selection (offline), and `eval_thresholds.yaml` holds
   real numbers — but CI only runs ruff + mypy + a `/health` smoke. This phase wires CI as two
   merge-blocking jobs: a hermetic **gates** job (deterministic eval gates via `run_evals`) and the
   existing service-provisioned **smoke** job extended to run the full `pytest` suite (unit + integration +
   red-team) — so the wall, grounding, red-team, and redaction checks block merge on regression while the
   merge gate stays deterministic. It also extends the RAG suite
   to report **MRR** (deterministic, gating) and **faithfulness + answer relevancy** (a frozen Groq judge,
   report-only — per the clarified decision, the non-deterministic judge never gates merges).

2. **Usable** — build the two front-end surfaces, which are currently empty scaffolding (0-byte files):
   the **cook React widget** (plain JS/JSX, Vite) that talks only to the existing backend
   (`/profile`, `/recipes`, `/recipes/{id}`, `/chat`, `/favorites`) attaching the `X-Profile-ID` header,
   and the **Streamlit operator dashboard** (corpus browse, on-demand eval runs, metrics, Phoenix
   deep-links) with a cookie login that survives refresh. The dashboard drives **new admin endpoints**
   (`/admin/corpus`, `/admin/evals/run`, `/admin/metrics`) behind `admin_deps.py`; operator credentials,
   the cookie-signing key, and a shared admin token all resolve from **Vault** at startup.

No new cook-facing logic and **no new Alembic migration** — the wall and grounding choke points are reused
unchanged; the widget only displays what the (already wall-filtered) backend returns.

## Technical Context

**Language/Version**: Python 3.12 (backend + dashboard + evals); Node 20 / Vite 5 for the widget (plain
JavaScript + JSX, **no TypeScript**, per the constitution and FR-022).

**Primary Dependencies**:
- *Backend (admin surface)*: FastAPI, SQLAlchemy, `hvac` (Vault) — all already present. Admin routers reuse
  existing `services/admin/*` package locations (currently empty) and existing `api/admin/*` files.
- *Dashboard*: `streamlit`, `streamlit-authenticator` (cookie login), `httpx`, `pandas` — **a new
  `dashboard` extra** must be added to `pyproject.toml` (it does not exist yet). These are UI deps,
  permitted by the "no new runtime deps beyond eval/test/UI" constraint (FR-030).
- *Evals (RAG judge)*: the frozen judge **reuses the existing `app.infra.llm_groq` adapter** (already a
  backend dep) with a pinned judge model id — **no new dependency**. `ragas` is intentionally *not*
  added; faithfulness/answer-relevancy are computed from (query, retrieved context, generated reply) and
  are report-only, so a heavy eval lib is unwarranted.
- *Widget*: `react`, `react-dom`, `vite`, `@vitejs/plugin-react` in `widget/package.json` (currently
  empty). UI deps, permitted by FR-030; the widget image is Node/Vite and shares no Python group.

**Storage**: No schema change. **No new Alembic migration.** Reads existing tables via existing
`repo/` helpers for the corpus browse (admin) and via existing user endpoints for the widget. Operator
secrets live in **Vault** (KV v2 `secret/sous-chef`), not in Postgres.

**Testing**: `pytest` (unit + integration + red-team) already present and the suites already exist; this
phase **wires them into CI** and adds a couple of UI-facing checks. The widget is validated by the
quickstart manual flow (no JS test framework added — keeping deps lean); the dashboard is validated by the
admin-endpoint integration check plus the quickstart login/refresh flow.

**Target Platform**: Linux containers via docker-compose locally; Railway for deploy. Widget is a static
bundle (Vite build) served as a static service; dashboard is its own Streamlit container (`dashboard`
extra); backend gains the admin routers in the same monolith image.

**Project Type**: Single FastAPI monolith plus two sibling front-end surfaces (per
`projectplanFolderForMd/structure.md`). The admin API/services are in-process modules of the monolith.

**Performance Goals**: Soft. Widget search feels responsive (one backend round-trip); planning turns show
progress (the agent path is ~15–20 s, surfaced as a distinct loading state, FR-023). Eval gate runtime in
CI stays modest (deterministic gates are pure/offline; the classifier gate trains a fast TF-IDF model).
No hard latency CI gate — the committed gates are correctness/safety/quality.

**Constraints**: The merge gate must stay **deterministic** (clarification): only hit@k, MRR, classifier
macro-F1, red-team (1.0), redaction (0 leaks), and smoke gate; the frozen judge is report-only. Operator
secrets never leave Vault (FR-028). The widget calls only the backend (FR-019) and attaches the profile
header on every request (FR-018). No image gains `torch`/`transformers`; no new datastore.

**Scale/Scope**: ~2,224 ingested recipes (existing corpus). New/changed code: `pyproject` `dashboard`
extra; `config` operator keys; `main.py` admin router registration; 3 admin API files + 4 admin service
files + `admin_deps.py`; Streamlit `app.py` + `auth.py` + 3 pages; the full widget (~12 files); RAG suite
extension (MRR + frozen-judge metrics) in `run_evals.py` + golden set; a CI `gates` job; Vault seed +
`.env.example` additions.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | How this plan complies |
|---|---|---|
| I Simplicity | PASS | No new DB tables/migration; reuse existing eval runner, wall, repos; admin auth is one shared Vault-issued token, not an auth system; widget is plain JSX with no state library. |
| II Build only required | PASS | Every artifact traces to an FR: gates→FR-001..012, widget→FR-013..023, dashboard→FR-024..029. No speculative features; plan/shopping/substitution rendering is in scope only because FR-016 already commits to it. |
| III Separation of concerns | PASS | Admin stays `api/admin → services/admin → repo`, gated by `admin_deps`; widget is a separate surface that only calls HTTP; dashboard calls `api/admin`, never the DB. The public widget cannot reach admin. |
| IV Testability | PASS | The wall/grounding/red-team/redaction tests become **merge-blocking** CI jobs (the central deliverable); admin endpoints get an integration check; thresholds stay committed and are never weakened. |
| V Reproducibility | PASS | Merge gate is deterministic (judge is report-only); thresholds committed; classifier artifact rebuilt in CI via `make train`; widget/dashboard pinned (`package.json`/`uv.lock`); fresh-clone smoke stays green. |
| VI Security & privacy | PASS | Operator password hash, cookie-signing key, and admin token all resolved from Vault (FR-028); admin endpoints behind `admin_deps`; redaction gate stays hard; widget profile-ID is non-secret scope only. |
| VII Maintainability | PASS | Small single-purpose files matching `structure.md`; every function commented; category mapping isolated to one module; ruff+mypy stay enforced (and now also gate via CI). |
| VIII Documentation-first | PASS | This plan + research/data-model/contracts/quickstart precede code; the category-spelling reconciliation is documented as a contract. |
| IX Spec-driven | PASS | Generated through the SpecKit cycle on-branch; clarifications resolved before planning. |
| X No unnecessary tech | PASS | Dashboard deps (streamlit/authenticator) and widget deps (react/vite) are UI; `ragas` deliberately **not** added (judge reuses Groq); no new datastore; no torch; no end-user auth. |

**Safety invariants**:
- **The wall is the grade** — untouched. The widget displays only backend-returned (already wall-filtered)
  data; admin corpus browse is read-only inspection. The red-team + wall-regression tests become CI gates.
- **Ground everything** — the widget never invents content; refusals and empty results render honestly
  (FR-020/FR-021). The frozen judge *measures* faithfulness but never relaxes grounding.
- **Hosted inference only / lean serving** — judge is a hosted Groq call (report-only); no weights added;
  no image gains torch.

**Result**: PASS — no violations; Complexity Tracking intentionally empty.

## Project Structure

### Documentation (this feature)

```text
specs/004-evals-and-uis/
├── plan.md              # This file
├── research.md          # Phase 0 — RAG metric extension, CI gate wiring, operator-auth model, widget runtime
├── data-model.md        # Phase 1 — config/return-shape entities (no DB schema change)
├── quickstart.md        # Phase 1 — runnable validation of all three stories
├── contracts/
│   ├── admin.openapi.yaml     # NEW: GET /admin/corpus, POST /admin/evals/run, GET /admin/metrics (admin-token auth)
│   └── ui-contracts.md        # NEW: widget→backend client contract (existing endpoints) + dashboard→admin + category map
└── checklists/
    └── requirements.md  # spec quality checklist (already green)
```

### Source Code (repository root)

Existing files reused **unchanged** unless noted. `(empty)` marks a 0-byte scaffold this phase fills.

```text
# ── PROVABLE: evals + CI ──────────────────────────────────────────────────────
evals/
├── run_evals.py                 # EXTEND: add gate_rag MRR (deterministic, gating) + a report-only
│                                #   faithfulness/answer-relevancy pass via a frozen Groq judge (SKIP w/o stack)
└── rag/golden.yaml              # REUSE as-is (judge scores from query+context+reply); OPTIONAL reference field
eval_thresholds.yaml             # EXTEND: add rag.mrr_min (deterministic gate); judge metrics are report-only (no key)
.github/workflows/ci.yml         # EXTEND: (a) NEW hermetic `gates` job — uv sync (backend+test+ml+evals) →
│                                #   make train → python -m evals.run_evals (deterministic gates, no services);
│                                #   (b) extend existing service-backed `smoke` job to run full pytest (unit+integration+redteam)
Makefile                         # REUSE (`make evals`, `make test` already defined)

# ── USABLE (operator): admin API + Streamlit dashboard ────────────────────────
app/
├── main.py                      # EDIT: register_admin_routers(app) alongside health + user routers
├── config.py                    # EDIT: add operator-auth settings sourced from Vault (hash, cookie key, admin token)
├── api/
│   ├── admin_deps.py            # (empty) FILL: require_operator dep — validate admin token (from Vault) on /admin/*
│   └── admin/
│       ├── corpus.py            # (empty) FILL: GET /admin/corpus — browse ingested recipes (paged)
│       ├── evals.py             # (empty) FILL: POST /admin/evals/run — run gates, return the results table
│       └── metrics.py           # (empty) FILL: GET /admin/metrics — classifier metrics + routing split + gate status
└── services/admin/
    ├── corpus.py                # (empty) FILL: read corpus via repo (read-only inspection)
    ├── evals.py                 # (empty) FILL: invoke evals.run_evals.run() → structured GateResult list
    ├── metrics.py               # (empty) FILL: classifier metrics (model card / testset) + routing split + CI gate status
    └── traces.py                # (empty) FILL: Phoenix UI deep-link base (cost viewed in Phoenix, not rolled up here)

dashboard/
├── app.py                       # (empty) FILL: entry — auth gate → landing; configures page/nav
├── auth.py                      # (empty) FILL: streamlit-authenticator cookie login; creds + cookie key from Vault
└── pages/
    ├── 1_corpus.py              # (empty) FILL: corpus browser (calls /admin/corpus)
    ├── 2_evals.py               # (empty) FILL: on-demand eval run + pass/fail vs threshold (calls /admin/evals/run)
    └── 3_metrics.py             # (empty) FILL: classifier metrics, routing split, gate status, Phoenix deep-links

# ── USABLE (cook): React widget (plain JS/JSX) ────────────────────────────────
widget/
├── package.json                 # (empty) FILL: react, react-dom, vite, @vitejs/plugin-react; scripts dev/build/preview
├── vite.config.js               # (empty) FILL: react plugin; dev proxy / VITE_API_BASE
├── jsconfig.json                # (empty) FILL: JS/JSX editor hints (no TS)
├── index.html                   # (empty) FILL: mount point
├── Dockerfile                   # (empty) FILL: vite build → static serve (nginx or vite preview)
└── src/
    ├── main.jsx                 # (empty) FILL: bootstrap React root
    ├── App.jsx                  # (empty) FILL: layout — constraints + chips + chat/results + favorites
    ├── api/client.js            # (empty) FILL: fetch wrapper; attaches X-Profile-ID; maps refused/empty/404 to UI states
    ├── lib/
    │   ├── profile.js           # (empty) FILL: generate/store passwordless profile ID (localStorage)
    │   └── categories.js        # NEW: canonical underscored values ↔ display labels; normalize spaced /chat form
    └── components/
        ├── CategoryChips.jsx    # (empty) FILL: 5 fixed category chips
        ├── ChatBox.jsx          # (empty) FILL: free-text input + send
        ├── RecipeCard.jsx       # (empty) FILL: title + key ingredients + save
        ├── RecipeDetail.jsx     # (empty) FILL: verbatim steps + nutrition + favorite toggle
        └── Favorites.jsx        # (empty) FILL: saved-recipes view
        # (added as needed) ConstraintsForm.jsx, MealPlanView.jsx, ShoppingList.jsx, RefusalNotice.jsx

# ── secrets / config plumbing ─────────────────────────────────────────────────
pyproject.toml                   # EDIT: add `dashboard` optional-dependency extra (streamlit, streamlit-authenticator, httpx, pandas)
scripts/seed_vault.sh            # EDIT: also seed OPERATOR_PASSWORD_HASH, DASHBOARD_COOKIE_KEY, ADMIN_API_TOKEN
.env.example                     # EDIT: add non-secret dashboard vars (BACKEND_ADMIN_URL, operator username, VITE_API_BASE doc)
docker-compose.yml               # EDIT: add dashboard + widget services (or document); wire dashboard→backend admin
```

**Structure Decision**: Single FastAPI monolith plus the two sibling surfaces, exactly as
`structure.md`. Three decisions carry the phase:

1. **Gates over new logic.** The "provable" half is almost entirely *wiring* — the eval runner and tests
   exist; the deliverable is a merge-blocking CI `gates` job plus the RAG metric extension. We do not
   reimplement what Phase 3 built; we enforce it.

2. **Deterministic merge gate, measured quality.** Hit@k, MRR, classifier macro-F1, red-team (1.0),
   redaction (0), and smoke gate; faithfulness/answer-relevancy are scored by a frozen Groq judge and
   reported only — so CI never flakes on a non-deterministic judge while quality is still tracked.

3. **One Vault-issued admin token is the API boundary; streamlit-authenticator is the human boundary.**
   The human logs into Streamlit (cookie survives refresh); the dashboard then calls `/admin/*` with a
   shared admin token resolved from Vault. The public widget holds no token and cannot reach admin
   (FR-029). All three operator secrets (password hash, cookie key, admin token) come from Vault (FR-028).

## Complexity Tracking

> No constitution violations; this section is intentionally empty.
