# Design — SousChef (v0.1.0)

How SousChef is built: the monolith's layering, the per-turn request flow (the safety choke points in
order), and the Railway deployment topology. This is the architecture entry point for a reviewer — pair it
with [DECISIONS.md](DECISIONS.md) (why, with numbers), [EVALS.md](EVALS.md) (the gates + latest results),
[SECURITY.md](SECURITY.md) (the safety model), and [RUNBOOK.md](RUNBOOK.md) (how to run/deploy).
Constitution principles are cited as `P#`; the project's golden rules as `#n`.

## 1. What it is

An AI recipe-discovery assistant for home cooks. A cook chats, gets **real retrieved recipes** (cards →
click for verbatim steps), can build a varied meal plan + a shopping list, and saves favorites. Two
properties dominate every design choice: **the wall is the grade** (never surface a recipe that violates a
cook's stated allergy/diet — enforced in deterministic code, not a prompt, golden rule #1) and **ground
everything** (the app never invents recipes or steps; lists come from retrieval, detail views render stored
steps verbatim, golden rule #2).

## 2. The monolith and its layers

One FastAPI app. Layering is strict — each layer only calls the one below it:

```
app/api/        thin HTTP. Split by audience:
                  api/user/*  (public, profile-scoped)
                  api/admin/* (operator-auth via admin_deps.py)
app/services/   business logic, split by audience:
                  services/user/   search, rag, freshness, the wall, meal_plan,
                                   shopping_list, nutrition, favorites
                  services/admin/  corpus, evals, metrics, ingestion, traces
                  services/shared/ recipe_view (the wall choke point), substitutions
app/repo/       the ONLY place that touches the DB — parameterized / ORM only (injection-safe, P3)
app/infra/      adapters for everything external (Groq, embeddings, Vault, Phoenix/LangSmith,
                Postgres, Redis, TheMealDB/TheCocktailDB/Open Food Facts) — swappable + mockable
app/agent/      the single bounded tool-calling agent (loop.py)
app/classifier/ the served intent router (TF-IDF + LogReg via joblib — no torch)
app/guardrails/ deterministic input/output rails
app/core/       redaction, config, shared primitives
```

**Other surfaces (same repo, separate apps):** `dashboard/` (Streamlit, operator-gated) · `widget/` (React
+ Vite, public). **Offline-only, never shipped in any image:** `ml/` (classifier training), `ingestion/`
(corpus pipeline), `evals/` (CI gates), `scripts/` (seed/export/load ops).

**No torch, ever** (golden rule #3, P3/P10): the LLM + embeddings are hosted-API calls; the classifier is
trained offline and served via `joblib`. Images stay < ~500MB.

## 3. The per-turn request flow

Every cook turn flows through the same ordered pipeline. The safety gates are deterministic code at fixed
choke points, so a new feature path cannot accidentally route around them:

```
                       cook message (untrusted free text)
                                   │
                    ┌──────────────▼───────────────┐
                    │ 1. guardrails INPUT rail      │  app/guardrails/input_rails.py
                    │    (deterministic, pre-route) │  refuse allergen/diet-override outright;
                    └──────────────┬───────────────┘  strip injection/jailbreak, serve safe remainder
                                   │
                    ┌──────────────▼───────────────┐
                    │ 2. intent classifier (router) │  app/classifier/predict.py
                    │    TF-IDF + LogReg (joblib)   │  6 labels; confidence-gated escalation
                    └───────┬───────────────┬───────┘
                  easy / high-conf │         │ hard / low-conf
                    ┌──────────────▼──┐   ┌──▼─────────────────────┐
                    │ 3a. workflow     │   │ 3b. bounded agent       │ app/agent/loop.py
                    │  (deterministic  │   │  5 schema-validated     │ capped iterations + token
                    │   service path)  │   │  tools; always-safe     │ budget; tools are the ONLY
                    └──────────────┬──┘   │  partial on bound       │ way the LLM acts
                                   │       └──┬─────────────────────┘
                                   └────┬─────┘
                    ┌───────────────────▼───────────────┐
                    │ 4. the WALL (constraint guard)     │  app/services/user/constraint_guard.py
                    │    every recipe → recipe_view      │  via app/services/shared/recipe_view.py
                    │    fail-closed, fresh allergen read │  over-fetch pool trimmed BEFORE top-3 (D4)
                    └───────────────────┬───────────────┘
                    ┌───────────────────▼───────────────┐
                    │ 5. guardrails OUTPUT rail          │  app/guardrails/output_rails.py
                    │    redact → re-assert the wall     │  redaction runs BEFORE logs AND before any
                    └───────────────────┬───────────────┘  trace span is emitted (golden rule #5)
                                        │
                                  response to cook
```

Key invariants of the flow:

- **The wall is downstream of routing on every path** — a misroute (workflow vs agent) degrades cost or
  quality, **never safety**. The output rail re-asserts the wall as defense-in-depth (see
  [SECURITY.md](SECURITY.md) §1–§3).
- **Grounding is structural** — cards/plans/steps render only stored rows; the LLM ranks/phrases the
  retrieved recipes and never invents; substitutions come from a curated, wall-filtered map (D6).
- **Retrieval** is one parameterized pgvector cosine query in `app/repo/recipes.py`, with category + diet +
  per-cook seen-history pushed into the same `WHERE` clause (freshness, D5). It **over-fetches a pool**, then
  the wall trims it before the cook sees the top-3 (D4) — so compliant cards surface even when violators rank
  higher.

## 4. Deployment topology (Railway)

One Railway **project** (`zonal-perception`), multiple services, expressed as one small TOML per service
(`railway/*.toml` + the root `railway.toml`) — native platform config, not Kubernetes/IaC sprawl (FR-012).
Local `docker-compose.yml` mirrors this 1:1 for parity (P5). Full table in
[data-model.md](../specs/007-ship-public-deploy/data-model.md) §1.

```
                         Public HTTPS (the only advertised surface)
              ┌──────────────────────────────┬───────────────────────────────┐
              │                               │
      ┌───────▼────────┐              ┌───────▼─────────┐
      │ widget          │  REST + CORS │ backend (API)    │  FastAPI monolith
      │ React+Vite→nginx│─────────────▶│ /health-gated    │  alembic upgrade head → serve on $PORT
      │ static host     │  X-Profile-ID│ deploy promotion │
      └─────────────────┘              └───┬───┬───┬──────┘
                                           │   │   │   (private network only — no public ingress)
                       ┌───────────────────┘   │   └───────────────────┐
                ┌──────▼───────┐         ┌──────▼──────┐         ┌───────▼────────┐
                │ PostgreSQL    │         │ Redis        │         │ Vault           │
                │ (pgvector)    │         │ (OPTIONAL —  │         │ server mode +   │
                │ plugin        │         │  cache only) │         │ persistent vol  │
                │ app `public`  │         └─────────────┘         │ seeded once     │
                │ + `phoenix`   │                                 └─────────────────┘
                │ schema (FR-011)│
                └───────────────┘

      Operator-gated, UNADVERTISED separate URLs (not the public surface, FR-001a):
      ┌─────────────────┐   ┌──────────────────────────────────────────────┐
      │ dashboard        │   │ tracing: self-hosted Phoenix (dev default) OR │
      │ Streamlit        │   │ LangSmith Cloud (prod — no Railway service,   │
      │ cookie auth      │   │ D11). Both via the redacting OTLP exporter.   │
      └─────────────────┘   └──────────────────────────────────────────────┘
```

- **Public surface = `widget` + `backend` API only.** `dashboard` and tracing are operator-gated on
  separate, unadvertised URLs (FR-001a). Postgres, Redis, and Vault are private-network only.
- **Deploy promotion is `/health`-gated**: a deploy is promoted only when `GET /health` returns 200 (all of
  Postgres + Vault reachable; Redis is checked only when configured). A 503 holds the last green rollout.
- **Data is identical local/CI/prod** via the committed seed corpus (`seeds/corpus/`): an offline exporter
  builds it, an at-deploy loader upserts it (network-free, idempotent on `source_id`), so production never
  runs the ingestion pipeline (FR-013). See [contracts/seed-corpus.md](../specs/007-ship-public-deploy/contracts/seed-corpus.md).
- **One Postgres serves both** the app (`public` schema) and Phoenix (`phoenix` schema) — zero additional
  datastores for tracing (FR-011, SC-008). **Tracing is non-blocking**: an outage leaves `/health` and the
  cook journey fully functional.

**Live URLs (v0.1.0):** widget `https://widget-production-5547.up.railway.app` · backend
`https://sous-chef-production-721e.up.railway.app` (`/health` → 200, postgres + vault `ok`).

## 5. Where the safety model lives

| Guarantee | Enforced in | Proven by |
|---|---|---|
| The wall (no violating recipe ever surfaces) | `services/user/constraint_guard.py` via `services/shared/recipe_view.py` (every path) + output-rail re-assertion | `tests/integration/test_wall_regression.py` (SC-006) |
| Manipulation refused | `guardrails/input_rails.py` (deterministic, pre-route) | `redteam.refusal_rate_min: 1.0` |
| No secret/PII in logs or spans | `core/redaction.py` before log + before span export | `redaction.leak_count_max: 0` |
| Secrets only in Vault; datastore creds platform-injected | `infra/vault.py` + the Railway/Vault split | `tests/unit/test_vault.py` + repo/image scan (SC-004) |
| Agent stays bounded | `agent/loop.py` iteration + token caps; Pydantic-validated tools | caps + agent-tool eval (SC-007) |

See [SECURITY.md](SECURITY.md) for the full threat model and [EVALS.md](EVALS.md) for the gate floors and
latest measured results.
