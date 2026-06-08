# Phase 1 Data Model: Foundation

This phase introduces **no product/business entities** — there are no recipes, profiles, or favorites yet
(those arrive in Phase 2). The only persistent concern is establishing the database so later phases have a
deterministic, migration-tracked schema to build on.

## Persistent schema

| Object | This phase | Notes |
|---|---|---|
| `vector` extension | Created by baseline migration `0001_baseline` | `CREATE EXTENSION IF NOT EXISTS vector;` — required before any pgvector column in Phase 3. |
| Application tables | **None** | No ORM models added; `app/models/*` stay empty placeholders. |
| Phoenix trace store | Managed by Phoenix | Phoenix owns its own tables in the shared Postgres via `PHOENIX_SQL_DATABASE_URL`; the app does not define or query them. |

**Migration baseline**: `alembic/versions/0001_baseline.py` enables the `vector` extension and otherwise
creates nothing. `alembic/env.py` targets `app.models` metadata (currently empty), so future phases add
tables by importing models and autogenerating revisions.

## Runtime (non-persistent) concepts

These are in-memory shapes the skeleton works with; they are not database entities.

### Settings (config.py)
Non-secret bootstrap values, validated at startup. Fields (all from environment / `.env.example`):
- `env` — environment name (e.g. `local`, `production`).
- `vault_addr`, `vault_token` — bootstrap access to Vault (the token itself is a non-secret dev token
  locally; in production it is injected by the platform, not committed).
- `postgres_url`, `redis_url`, `phoenix_collector_endpoint` — service locations.
- Validation: missing required field → fail-fast error at construction (FR-010).

### Resolved secrets (from Vault, never persisted to disk/logs)
Loaded once at startup by `infra/vault.py` from a KV path; held in memory only. In this phase the set is
minimal (placeholders proving the path), e.g. a sample app secret; real provider keys (Groq, embeddings)
are added in their phases. Never written to code, config, images, logs, or traces (FR-004/FR-005/FR-007).

### Health status (api/health.py response)
A computed, non-persistent readiness view:
- `status`: `"ok"` (HTTP 200) when all dependencies reachable, else `"unhealthy"` (HTTP 503).
- `dependencies`: map of `{ postgres, redis, vault }` → `"ok" | "unreachable"`.
- `version` / `env`: informational, non-secret.

### Request trace (emitted to Phoenix)
One span tree per application request, produced by the tracing middleware/instrumentation, passed through
`core.redaction.redact` before export. Not stored or queried by the app.

## Relationships

None to model in this phase. The only structural guarantee established is: **`repo/` + Alembic are the
sole path to the database**, and the database is reachable with the `vector` extension present, so Phase 2
can add `recipes`, `ingredients`, `profiles`, `favorites`, and `seen_history` on a clean baseline.
