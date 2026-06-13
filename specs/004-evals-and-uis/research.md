# Phase 0 Research: Provable & Usable — Gated Evals + the Two UIs

This phase is mostly *wiring and UI*, so research resolves the few real decisions: how to extend the RAG
suite, how to make the existing gates merge-blocking in CI, the operator-auth model, and the widget/
dashboard runtime shape. Each item: Decision → Rationale → Alternatives considered.

## R1 — RAG suite: add MRR (gating) + faithfulness/answer-relevancy (report-only)

**Decision**: Extend `gate_rag` in `evals/run_evals.py` to also compute **MRR** over the existing golden
set (deterministic, gating against new `rag.mrr_min`). Add a separate, report-only pass that scores
**faithfulness** and **answer relevancy** with a **frozen Groq judge** (a pinned judge model id, reused
from `app.infra.llm_groq`), computed from `(query, retrieved context, generated reply)`. The judge pass is
**offline** (needs a live stack + provider key) and **SKIPs** cleanly otherwise; it never sets the exit
code. Per the clarification, only hit@k and MRR gate merges.

**Rationale**: Hit@k and MRR are pure functions of the retrieval ranking — reproducible, no network, safe
as merge gates. Faithfulness/answer-relevancy require a model judge whose scores drift run-to-run; making
them merge gates would flake CI and block unrelated work (the exact risk the clarification avoided).
Reusing the existing Groq adapter means **zero new dependencies** (constraint FR-030) and a single pinned
model keeps scores comparable over time ("frozen" judge). The golden set already carries `ideal` recipe
ids, which is all MRR needs (reciprocal rank of the first ideal among the surfaced cards).

**Alternatives considered**:
- *Add `ragas` to the `evals` group* — rejected: a heavy eval lib for two report-only numbers; the Groq
  adapter already exists and the constraint discourages new deps. `ragas` remains a documented future
  option if richer metrics are wanted.
- *Hand-labeled faithfulness/relevancy only* — rejected: doesn't scale and was option D the user declined.
- *Make all four metrics gate* — rejected by the clarification (non-deterministic merge gate).

## R2 — Make the existing gates merge-blocking in CI

**Decision**: Wire CI as **two jobs**, keeping the merge gate deterministic and hermetic. **(a) `gates`**
(no service containers): `uv sync --frozen --extra backend --group test --group ml --group evals` →
`make train` (rebuild `ml/artifacts/model.joblib`, fast TF-IDF, no torch) → `uv run python -m
evals.run_evals` — runs the deterministic gates (classifier macro-F1, red-team refusal=1.0, redaction
leaks=0); the offline RAG/agent/judge gates SKIP without keys/corpus. **(b)** extend the **existing
service-provisioned `smoke` job** (it already stands up Postgres+Redis+Vault) to run the full
`uv run pytest tests/unit tests/integration tests/redteam -q` after the `/health` check, since the
integration suite (chat flow, favorites, wall regression, admin) needs a live stack. Both jobs run on
`pull_request` + `push: main`; branch protection requires both green to merge (FR-003/004/005). This split
(decided in analyze finding F1) avoids running stack-dependent integration tests in a job with no stack,
while the pure safety gates (red-team, redaction) run in BOTH the `gates` job (via `run_evals`) and the
`smoke` job (via pytest) — belt and suspenders.

**Rationale**: The hard safety gates (red-team, redaction) are pure/offline and already implemented in
both `pytest` and `run_evals` — they just aren't *run* in CI yet. Running them is the central deliverable.
The classifier macro-F1 gate needs the joblib artifact, which is gitignored; training it in CI (seconds
for TF-IDF + logistic regression) keeps the gate real without committing a binary. The existing `smoke`
job stays as-is.

**Alternatives considered**:
- *Commit `model.joblib` via Git LFS* — rejected: binary in the repo; training in CI is fast and matches
  reproducibility (P V, the artifact is rebuilt from pinned data).
- *Run RAG/agent gates in CI with live providers* — rejected: needs secrets + an embedded corpus in CI,
  is non-deterministic and slow; offline SKIP is the existing, intended design of `run_evals`.
- *Separate workflow file* — rejected: one `ci.yml` with parallel jobs (ruff, mypy, smoke, gates) is
  simpler and already the pattern.

## R3 — Operator authentication model (two boundaries, secrets from Vault)

**Decision**: Two boundaries. **Human → dashboard**: `streamlit-authenticator` with a single operator
credential; the password **hash** and the **cookie-signing key** are read from **Vault** at dashboard
startup (via the same KV path the backend uses) and assembled into the authenticator config in memory, so
the cookie survives refresh and nothing sensitive is committed (FR-028). **Dashboard → backend admin**:
the dashboard attaches a shared **admin token** (also from Vault) on every `/admin/*` call; `admin_deps.py`
exposes a `require_operator` dependency that compares the presented token to the Vault-loaded value and
returns 401/403 otherwise. The public widget never holds this token, so it cannot reach admin (FR-029).

**Rationale**: Streamlit owns the *human* session (cookie persistence is exactly what `streamlit-
authenticator` solves). The backend admin endpoints are a *machine* boundary and need their own guard
independent of Streamlit; a single shared bearer token from Vault is the simplest control that satisfies
"public widget can't reach admin" without standing up an auth system (P I, P X). All three secrets live in
Vault, consistent with the constitution and the clarification.

**Alternatives considered**:
- *streamlit-authenticator config file with a committed hash* — rejected by the clarification (secrets to
  Vault, nothing committed).
- *Full OAuth/JWT for admin* — rejected: end-user auth is explicitly out of scope (P X); one operator.
- *No backend admin guard (rely on Streamlit only)* — rejected: the backend is a separate service reachable
  independently; it must guard itself.

## R4 — "Run eval suites on demand" from the dashboard

**Decision**: `POST /admin/evals/run` → `services/admin/evals.py` calls `evals.run_evals.run()` in-process
and returns the structured `GateResult` list (name, status PASS/FAIL/SKIP, detail) as JSON; `pages/
2_evals.py` renders it as a table with pass/fail vs threshold. The deterministic gates run synchronously
(fast); the offline gates report SKIP unless the dashboard host has the corpus + keys. Thresholds shown
come from `eval_thresholds.yaml` (single source of truth).

**Rationale**: `run_evals.run()` already returns exactly this shape and already separates deterministic
from offline gates with clean SKIPs — reuse it rather than reimplement. Synchronous is acceptable for an
operator-triggered action; no job queue needed (P I).

**Alternatives considered**:
- *Shell out to `make evals` as a subprocess* — rejected: importing `run()` is cleaner, returns
  structured results, and avoids parsing stdout.
- *Background task + polling* — rejected: over-engineering for a single operator and a fast gate set.

## R5 — Metrics & Phoenix deep-links

**Decision**: `GET /admin/metrics` → `services/admin/metrics.py` returns: classifier metrics (read from the
model card / a quick score on `evals/classifier/testset.csv`), the workflow-vs-agent **routing split**, and
the **CI gate status** (last `run_evals` summary). The routing split is read from a **lightweight Redis
counter** the router increments per decision (`routing:workflow` / `routing:agent`) — a tiny addition to
`services/user/router.py`, no new dependency (Redis is already present), and a concrete, buildable source
(decided in analyze finding C1; the earlier "expose a counter or compute from traces" was unspecified).
`services/admin/traces.py` returns the **Phoenix UI deep-link** base only; per-turn token cost is viewed in
the Phoenix UI, which owns trace + cost storage — the dashboard does **not** roll up a cost number itself
(analyze finding C2). `pages/3_metrics.py` renders the metrics and links out to Phoenix for full
traces/cost.

**Rationale**: Phoenix owns trace storage and its own rich UI (per the design docs) — the dashboard
*summarizes and deep-links*, it does not rebuild tracing (P I, P X). Routing split and gate status are the
operator-relevant numbers for "is the hybrid working / is the build healthy".

**Alternatives considered**:
- *Embed Phoenix iframes / proxy its API* — rejected: deep-links are simpler and Phoenix already serves
  its own UI.
- *Persist a metrics table* — rejected: no new schema; derive on read.

## R6 — Widget runtime, identity, and build

**Decision**: Vite + React, **plain JS/JSX** (no TS). `widget/package.json` pins `react`, `react-dom`,
`vite`, `@vitejs/plugin-react`. Backend base URL from `import.meta.env.VITE_API_BASE` (dev proxy in
`vite.config.js`). Identity: `lib/profile.js` generates a UUID with `crypto.randomUUID()` on first load and
stores it in `localStorage`; `api/client.js` attaches it as `X-Profile-ID` on every request and calls only
`VITE_API_BASE` (FR-018/019). The widget consumes the **existing** 002/003 endpoints unchanged. The image
is a Vite static build served by a tiny static server (nginx or `vite preview`) via `widget/Dockerfile`.

**Rationale**: This is the stack the constitution fixes and the scaffold already names. `crypto.randomUUID`
needs no dependency. Keeping the widget "dumb" (display only) preserves grounding/safety server-side
(FR-032). No JS test framework is added (lean deps); the quickstart manual flow + the backend gates are
the validation.

**Alternatives considered**:
- *Add a state library / UI kit / fetch lib (axios)* — rejected: `fetch` + React state suffice at this
  scope (P I, P X). A light CSS approach (plain CSS modules) over a heavy design system.
- *TypeScript* — prohibited by the constitution and FR-022.

## R7 — Category representation reconciliation

**Decision**: `widget/src/lib/categories.js` is the single source: an ordered list of the five canonical
**underscored** values (`hot_drink`, `cold_drink`, `breakfast`, `lunch`, `dinner`) each mapped to a display
label ("Hot Drink", …). The catalog endpoints (`/recipes`, `/favorites`) already use underscored values, so
the chip→cards path needs no translation; any category string arriving from `/chat` (spaced form) is
normalized to underscored on input (`replace(" ", "_")`). The cook only ever sees display labels.

**Rationale**: Matches the clarification and the backend reality (the eval runner already does the same
spaces→underscores normalization for the golden set, confirming underscored is the internal canonical
form). Isolating the mapping to one module makes the wire discrepancy a single, testable choke point.

**Alternatives considered**:
- *Canonicalize on spaced form* — rejected: the catalog endpoints the chips call use underscores; spaced
  would require translating the more common path.
- *Per-component ad-hoc mapping* — rejected: scatters the discrepancy and invites raw tokens leaking to UI.

## R8 — Dependency & secrets plumbing (no new runtime surface beyond UI/eval)

**Decision**: Add a `dashboard` **extra** to `pyproject.toml` (`streamlit`, `streamlit-authenticator`,
`httpx`, `pandas`) — it does not exist yet. Add `react`/`vite` to `widget/package.json`. Extend
`scripts/seed_vault.sh` to also write `OPERATOR_PASSWORD_HASH`, `DASHBOARD_COOKIE_KEY`, and
`ADMIN_API_TOKEN` to `secret/sous-chef` (dev placeholders by default, real values forwarded from the
operator env like the existing provider keys). Add non-secret dashboard/widget vars to `.env.example`
(`BACKEND_ADMIN_URL`, operator username, `VITE_API_BASE` doc). No backend runtime dep is added; `ragas` is
not added.

**Rationale**: Dashboard libs are UI (permitted by FR-030); the backend image is unchanged (admin routers
use libs already in the `backend` extra). Seeding the operator secrets alongside the provider keys keeps
the Vault discipline uniform and a fresh `make up && make seed` boots both surfaces.

**Alternatives considered**:
- *Put operator creds in `.env`/compose* — rejected: violates "secrets in Vault" and the clarification.
- *A separate Vault path for operator secrets* — acceptable but unnecessary; one app path is simpler.

## Resolved unknowns

All Technical Context items are resolved; there are **no remaining NEEDS CLARIFICATION**. Key confirmations
from inspecting the repo: the eval runner, thresholds, eval data, and pytest suites already exist; CI runs
only ruff/mypy/smoke today; `dashboard/`, `app/api/admin/*`, `app/services/admin/*`, and all of `widget/`
are empty scaffolds; `main.py` mounts only health + user routers; `config.py` has no operator keys;
`pyproject.toml` has no `dashboard` extra and the `evals` group has no `ragas`; `model.joblib` is gitignored.
