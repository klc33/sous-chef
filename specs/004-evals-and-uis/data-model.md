# Phase 1 Data Model: Provable & Usable — Gated Evals + the Two UIs

This feature adds **no database schema** and **no Alembic migration**. The "entities" below are
configuration values, return shapes, and client-side state — not new tables. Existing tables
(`recipes`, `ingredients`, `profiles`, `favorites`, `seen_history`, the `recipes.embedding` column) are
read through existing `repo/` helpers and existing user endpoints, unchanged.

## Configuration & secrets (Vault / settings)

### Operator Auth Settings (`app/config.py`, values from Vault)
Resolved at startup from Vault KV `secret/sous-chef`; never committed or imaged.

| Field | Source | Purpose |
|---|---|---|
| `operator_username` | `.env.example` (non-secret) | The single operator's login name on the dashboard. |
| `operator_password_hash` | Vault `OPERATOR_PASSWORD_HASH` | bcrypt hash checked by `streamlit-authenticator`. |
| `dashboard_cookie_key` | Vault `DASHBOARD_COOKIE_KEY` | Signs the session cookie so login survives refresh. |
| `admin_api_token` | Vault `ADMIN_API_TOKEN` | Shared bearer token the dashboard sends; validated by `admin_deps`. |

**Validation rules**: all three Vault values MUST be present at startup of the surface that needs them
(backend needs `admin_api_token`; dashboard needs all three). Missing → fail fast (consistent with the
existing Vault fail-fast in `main.py`). Dev placeholders are seeded by `scripts/seed_vault.sh`.

### Eval Threshold keys (`eval_thresholds.yaml`)
Existing keys reused; **one new key** added.

| Key | Status | Gate? |
|---|---|---|
| `classifier.f1_min` (0.90) | existing | hard gate (deterministic) |
| `rag.hit_at_k_min` (0.80), `rag.k` (3) | existing | hard gate (deterministic) |
| **`rag.mrr_min`** | **NEW** (default ~0.70, tightened to measured) | hard gate (deterministic) |
| `redteam.refusal_rate_min` (1.0) | existing | hard gate (deterministic) |
| `redaction.leak_count_max` (0) | existing | hard gate (deterministic) |
| `smoke.must_pass` (true) | existing | hard gate |
| *(faithfulness / answer_relevancy)* | report-only | **not gated** (no threshold key) |

## Eval return shapes (in-process)

### GateResult (existing, reused)
`{ name: str, status: "PASS"|"FAIL"|"SKIP", detail: str }` — produced by `evals/run_evals.py` and
returned verbatim by `POST /admin/evals/run`. The RAG extension adds two report-only rows
(`rag faithfulness`, `rag answer-relevancy`) with `SKIP` when no live stack/judge is available.

### JudgeScore (new, report-only)
Computed per golden case by the frozen Groq judge: `{ case_id, faithfulness: 0..1, answer_relevancy: 0..1 }`.
Aggregated to a mean reported in the gate `detail`; never persisted, never gates.

## Admin API return shapes (new)

### CorpusPage (`GET /admin/corpus`)
`{ items: RecipeCardAdmin[], total: int, page: int, page_size: int }` where `RecipeCardAdmin` =
`{ id, title, category (underscored), cuisine?, source, source_id, allergens[], diet_flags[] }`.
Read-only projection of existing recipe rows for operator inspection (more fields than the cook card,
since the operator may see allergen/diet tags).

### EvalRunResult (`POST /admin/evals/run`)
`{ gates: GateResult[], thresholds: object, ran_at: ISO-8601 }` — `thresholds` echoes
`eval_thresholds.yaml` so the page shows measured-vs-floor side by side.

### MetricsSummary (`GET /admin/metrics`)
`{ classifier: { macro_f1: number, per_class: object }, routing: { workflow_pct: number,
agent_pct: number, total_turns: int }, gates: GateResult[] (last run summary),
phoenix: { ui_base_url: string|null, trace_deep_link?: string } }`.
- **routing** is derived from a lightweight Redis counter the router increments per decision
  (`routing:workflow` / `routing:agent`); `total_turns` is their sum. No new dependency, no new table.
- **phoenix** is **deep-link only** — per-turn token cost is viewed in the Phoenix UI (which owns trace +
  cost storage); the dashboard does not roll up a cost number itself (analyze C2).

## Client-side state (widget, browser only)

### ProfileIdentity (`localStorage`)
`{ profileId: string (UUID) }` — generated once via `crypto.randomUUID()`; attached as `X-Profile-ID` on
every request. Non-secret; scopes favorites + seen-history only. No server entity beyond the existing
`profiles` row the backend lazily ensures.

### ConstraintsCache (widget UI state, mirrors `/profile`)
`{ diet, allergies[], default_servings }` — fetched from `GET /profile`, edited via `PUT /profile`. The
backend is the source of truth; the widget caches for display. Drives nothing safety-related client-side
(the wall is server-side).

### CategoryMap (`widget/src/lib/categories.js`, static)
Ordered canonical entries: `[{ value: "hot_drink", label: "Hot Drink" }, { value: "cold_drink", label:
"Cold Drink" }, { value: "breakfast", label: "Breakfast" }, { value: "lunch", label: "Lunch" },
{ value: "dinner", label: "Dinner" }]`. Plus `normalize(s)` → underscored (`s.replace(/ /g, "_")`) for any
spaced category string arriving from `/chat`. The cook only ever sees `label`.

### ChatTurnView (widget render state, mirrors `/chat` response)
`{ reply, intent, refused, recipes[], meal_plan?, shopping_list?, substitution? }` from the existing
`ChatResponse` contract. Render branches: `refused` → RefusalNotice; `recipes` non-empty → card grid;
empty → honest empty state; `meal_plan`/`shopping_list`/`substitution` present → their respective views.

## Relationships & lifecycle

- **No new persistence.** Operator secrets live in Vault; eval results are computed on demand and not
  stored; widget identity/constraints live in the browser + existing backend tables.
- **Lifecycle**: operator logs in (cookie set, survives refresh) → calls admin endpoints with the admin
  token → reads corpus/eval/metrics. Cook generates a profile id once → it persists across sessions in
  `localStorage` → favorites/seen-history persist server-side keyed to it.
- **No state transitions** beyond authenticated/unauthenticated (operator) and the existing recipe/
  favorite lifecycle (unchanged).
