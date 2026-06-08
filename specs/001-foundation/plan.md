# Implementation Plan: Foundation — Runnable, Reproducible, Secure Skeleton

**Branch**: `001-foundation` | **Date**: 2026-06-08 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-foundation/spec.md`

## Summary

Stand up the Sous-Chef monolith skeleton so that a fresh clone boots the full stack with one command,
exposes a single readiness `/health` endpoint, resolves every secret from Vault, and emits a redacted
trace per request — with CI (lint + type-check + a full-stack smoke test) green and a hello-world build
deployable to Railway. No cook-facing product logic ships in this phase.

Technical approach: a FastAPI app factory wires Pydantic settings (non-secrets), a Vault adapter that
loads all secrets at startup, SQLAlchemy/Alembic against Postgres+pgvector, a Redis client, and an
OpenTelemetry/OpenInference exporter to a self-hosted Phoenix service (Phoenix persists its own data to
the same Postgres). A redaction utility in `core/` is invoked by both the logging config and the tracing
span processor so no secret or PII leaves through either path. `docker-compose.yml` runs five services
(backend + postgres + redis + vault-dev + phoenix); the backend image installs only the `backend` extra
via `uv` (no torch). The layout follows the agreed monolith tree in `projectplanFolderForMd/structure.md`.

## Technical Context

**Language/Version**: Python 3.12 (image base `python:3.12-slim`; `requires-python >= 3.11`).

**Primary Dependencies** (`backend` extra only): FastAPI, `uvicorn[standard]`, Pydantic +
pydantic-settings, SQLAlchemy, Alembic, `psycopg[binary]`, pgvector, redis, hvac (Vault),
opentelemetry-sdk, openinference-instrumentation, arize-phoenix-otel, structlog. Presidio
(analyzer/anonymizer) is present in the `backend` extra but in this phase the redaction utility is a
thin stub with the Presidio wiring deferred to Phase 3 when real input paths exist.

**Storage**: PostgreSQL 16 + pgvector extension (one database instance, reused by Phoenix for its trace
store via `PHOENIX_SQL_DATABASE_URL`). Redis 7 for cache/session (no product use yet). Schema managed by
Alembic; this phase ships only a baseline migration that enables the `vector` extension.

**Testing**: pytest + pytest-asyncio + httpx (`test`/`dev` groups). One smoke test boots the app against
the running stack and asserts `/health` reports healthy.

**Target Platform**: Linux containers locally via docker-compose; Railway for the deployed backend
(HTTPS, health-check-gated deploy).

**Project Type**: Single backend web service (monolith) with sibling surfaces (dashboard, widget) not
touched in this phase.

**Performance Goals**: `/health` returns within 1 second when healthy (SC-002). No throughput targets at
this phase.

**Constraints**: One-command fresh-clone startup (SC-001); zero secret values in logs/traces (SC-003);
a trace per application request (SC-004); no torch in any image; small images; secrets only in Vault.

**Scale/Scope**: Skeleton only — one service, a handful of infra adapters, one baseline migration, one
smoke test. No business entities yet.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | How this plan complies |
|---|---|---|
| P1 Simplicity | PASS | Monolith, docker-compose, pgvector-in-Postgres, no orchestration sprawl. |
| P2 Build only required | PASS | Only `/health`, infra adapters, tracing, CI, deploy — no product logic. |
| P3 Separation of concerns | PASS | `api → services → repo → infra` honored; only `infra/` touches externals, only `repo/`+Alembic touch the DB. No `services/` logic added yet. |
| P4 Testability | PASS | Adapters are mockable; a full-stack smoke test gates CI. Safety gates (red-team/redaction) arrive with their features; redaction utility is testable now. |
| P5 Reproducibility | PASS | One-command compose; pinned `uv.lock`; Alembic baseline; committed `eval_thresholds.yaml` placeholders. |
| P6 Security & privacy | PASS | All secrets from Vault; `.env.example` non-secrets only; redaction before logs AND trace spans; parameterized DB access; no bounded-loop concern yet. |
| P7 Maintainability | PASS | Small single-purpose files matching `structure.md`; structured logging; lint + mypy. |
| P8 Documentation-first | PASS | This plan + spec + research/data-model/quickstart precede code. |
| P9 Spec-driven | PASS | Generated via SpecKit phase cycle; artifacts committed. |
| P10 No unnecessary tech | PASS | Only the approved stack; no torch/transformers; no dedicated vector DB; no Kubernetes; no end-user auth. |

**Safety invariants**: the wall and grounding are not exercised this phase (no recipes); hosted-inference
and lean-classifier rules are respected by not importing any model runtime. Redaction-before-emit is
implemented now as required by P6.

**Result**: PASS — no violations; Complexity Tracking left empty.

## Project Structure

### Documentation (this feature)

```text
specs/001-foundation/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── health.openapi.yaml   # the GET /health contract
└── checklists/
    └── requirements.md  # spec quality checklist (from /speckit-specify)
```

### Source Code (repository root)

The monolith tree already exists as empty placeholders (per `projectplanFolderForMd/structure.md`). This
phase fills **only** the files below; all other placeholders stay empty until their feature phase.

```text
app/
├── main.py                 # app factory: settings → vault → logging → tracing → routers → lifespan
├── config.py               # Pydantic settings (non-secrets: service URLs, Vault addr, env name)
├── api/
│   └── health.py           # GET /health — readiness: checks Postgres, Redis, Vault reachable
├── infra/
│   ├── vault.py            # hvac adapter: load all secrets at startup; resolve(key) accessor
│   ├── db.py               # SQLAlchemy engine + session factory + ping() for readiness
│   ├── cache.py            # Redis client + ping() for readiness
│   └── tracing.py          # OTel/OpenInference setup → Phoenix exporter; redacting span processor
└── core/
    ├── logging.py          # structlog config with a redaction processor in the chain
    ├── redaction.py        # redact(text)/redact_mapping(...) stub used by logging AND tracing
    └── errors.py           # error types + FastAPI handlers (clean 4xx/5xx, fail-fast startup errors)

alembic/
├── env.py                  # Alembic env pointed at app.models metadata
├── script.py.mako          # migration template
└── versions/0001_baseline.py   # baseline: CREATE EXTENSION IF NOT EXISTS vector
alembic.ini                 # Alembic config (DB URL from settings/Vault at runtime)

# Repo-root infra & ops
pyproject.toml              # uv: base + backend extra + dev/test groups (see dependencies.md); no torch
uv.lock                     # generated, pinned
Dockerfile                  # backend image: uv sync --frozen --no-dev --extra backend
docker-compose.yml          # 5 services: backend, postgres(pgvector), redis, vault(dev), phoenix
railway.toml                # build + start + /health healthcheck
Makefile                    # up / down / test / lint / seed (foundation-relevant targets)
.env.example                # non-secret bootstrap only: VAULT_ADDR, VAULT_TOKEN, *_URL, ENV
eval_thresholds.yaml        # placeholder thresholds (filled in later phases)
.github/workflows/ci.yml    # ruff → mypy → full-stack smoke test
scripts/seed_vault.sh       # seed dev Vault with the app's secrets on boot
tests/
├── conftest.py             # fixtures: app client, settings override
└── integration/test_health_smoke.py   # boots app vs running stack, asserts /health healthy
```

**Structure Decision**: Single FastAPI monolith exactly as captured in
`projectplanFolderForMd/structure.md`. This phase touches only `app/{main,config}.py`,
`app/api/health.py`, `app/infra/{vault,db,cache,tracing}.py`, `app/core/{logging,redaction,errors}.py`,
`alembic/*`, the root infra/ops files, `scripts/seed_vault.sh`, and one smoke test. No `services/`,
`repo/`, `models/` product code is added (the baseline migration needs no ORM models yet).

## Complexity Tracking

> No constitution violations; this section is intentionally empty.
