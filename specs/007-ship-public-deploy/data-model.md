# Phase 1 Data Model: Ship to a Public URL (v0.1.0)

This feature adds no new application database tables. Its "data model" is the **deployment topology**, the
**secret keyspace**, and the **seed-corpus artifact** schema. Each maps to a spec entity and the FRs.

---

## 1. Deployment topology (maps to entity *Deployment Environment*)

One Railway **project** containing:

| Unit | Kind | Exposure | Source | Notes |
|------|------|----------|--------|-------|
| `backend` | Service | **Public** (API origin) | root `railway.toml` + `Dockerfile` | FastAPI monolith; `/health` gated deploy. |
| `widget` | Service | **Public** (the advertised URL) | `railway/widget.toml` + `widget/Dockerfile` | Vite build → nginx static; `VITE_API_BASE` = public backend origin (build arg). |
| `dashboard` | Service | **Operator-gated** (separate, unadvertised URL) | `railway/dashboard.toml` + `dashboard/Dockerfile` | Streamlit; behind streamlit-authenticator cookie login. |
| `phoenix` | Service | **Operator-gated** (separate, unadvertised URL) | `railway/phoenix.toml` (image) | Tracing UI; same Postgres, `phoenix` schema. |
| `vault` | Service | Private network only | `railway/vault.toml` (image) | Server mode + **persistent volume**; seeded once by operator. |
| PostgreSQL (pgvector) | Plugin | Private network only | Railway plugin | App `public` schema + Phoenix `phoenix` schema (FR-011). |
| Redis | Plugin | Private network only | Railway plugin | Cache / session store. |

**Invariants**
- Only `backend` (API) and `widget` are reachable on the public URL(s) (FR-001/FR-001a).
- `dashboard` and `phoenix` are deployed but unadvertised and operator-gated (FR-001a).
- Postgres, Redis, and Vault are private-network only (no public ingress).
- Local `docker-compose.yml` mirrors this 1:1 for parity (P5); `pgadmin` stays local-profile-only and is
  never deployed.

**State / lifecycle**: a deploy of `backend` runs `alembic upgrade head` → (prod) corpus already loaded /
(first deploy) `load_seed_corpus.py` → serve; the deploy is **promoted only when `/health` returns 200**
(all of Postgres/Redis/Vault reachable). A failed health check holds the rollout on the last green
deployment (Edge case: gate-fail-after-green).

---

## 2. Secret keyspace (maps to entity *Secret*)

Two stores, no overlap. **Nothing secret is ever a Railway variable.**

### 2a. Railway variables — **bootstrap only, non-secret** (FR-005/FR-006)
| Variable | Meaning | Source |
|----------|---------|--------|
| `ENV` | `production` | static |
| `VAULT_ADDR` | Vault service private address | Railway reference to the vault service |
| `VAULT_TOKEN` | Vault auth token | platform-injected (treated as bootstrap, not an app secret) |
| `POSTGRES_URL` | Managed Postgres connection URL | **platform-injected** by the Postgres plugin |
| `REDIS_URL` | Managed Redis connection URL | **platform-injected** by the Redis plugin |
| `PHOENIX_COLLECTOR_ENDPOINT` | Tracing collector address | Railway reference |
| `LLM_PROVIDER`, model knobs | Non-secret provider/model selection | static (defaults in `app/config.py`) |
| `WIDGET_ORIGINS` | CORS allow-list incl. the deployed widget origin | static |
| `BACKEND_ADMIN_URL`, `OPERATOR_USERNAME` | Dashboard non-secrets | static |

### 2b. Vault `secret/sous-chef` (KV v2) — **the only home for app secrets** (FR-004)
Exactly the keys `app/infra/vault.py` + `scripts/seed_vault.sh` already use:

| Vault key | Used by | Notes |
|-----------|---------|-------|
| `GROQ_API_KEY` | LLM (chat/agent) | provider key |
| `EMBEDDINGS_API_KEY` | embeddings provider | provider key |
| `OPENAI_API_KEY` | LLM when `LLM_PROVIDER=openai` | dormant under default `groq` |
| `OPERATOR_PASSWORD_HASH` | dashboard auth | bcrypt hash |
| `DASHBOARD_COOKIE_KEY` | dashboard cookie signing | |
| `ADMIN_API_TOKEN` | backend `/admin/*` guard | backend fails fast if absent |
| `app_secret` | misc app secret slot | existing |

**Rule**: these values are present in repo/image/`.env` = **never**; in Railway variables = **never**;
in Vault = **always** (FR-004, SC-004). The example env file stays non-secret only (FR-006).

---

## 3. Seed-corpus artifact (maps to entities *Demo Scenario* corpus + *Deployment Environment* data)

Committed under `seeds/corpus/`; produced offline by `export_seed_corpus.py`, consumed by
`load_seed_corpus.py` (see [contracts/seed-corpus.md](contracts/seed-corpus.md)).

| File | Schema | Purpose |
|------|--------|---------|
| `recipes.jsonl` | one JSON object per recipe: `source_id`, `title`, `category` (one of the 5 fixed), `ingredients[]`, `steps`, `diet_tags[]`, `allergen_tags[]`, + remaining `recipes`-table columns | the recipe rows |
| `embeddings.npy` | float32 matrix `[N, D]`, row *i* ↔ recipe *i* | pre-computed query-space vectors |
| `manifest.json` | `{ embedding_model, dim, count, exported_at, git_sha }` | pins the embedding space + provenance |

**Mapping to existing tables**: rows load into `recipes` (and child `ingredients` / tag tables) via the
repo layer; vectors load into the pgvector column. Load is **idempotent upsert on `source_id`**.

**Validation rules**
- `len(recipes.jsonl) == embeddings.npy.shape[0] == manifest.count`.
- `embeddings.npy.shape[1] == manifest.dim` and `manifest.embedding_model` matches the runtime embeddings
  model (so seeded vectors and live query vectors share one space — else the loader fails fast).
- every recipe has exactly one of the five fixed categories (`hot drink | cold drink | breakfast | lunch
  | dinner`) — the corpus invariant from earlier features.
- the subset is large enough that the demo scenario and the RAG golden set return real results (FR-013).
