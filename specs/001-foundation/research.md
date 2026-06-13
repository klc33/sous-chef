# Phase 0 Research: Foundation

All Technical Context items resolved — no remaining NEEDS CLARIFICATION. The spec's clarification session
already settled the three behavioral unknowns (Vault dev mode, full-stack CI smoke, single `/health`).
This document records the technology decisions and the patterns the implementation will follow.

## Decision 1 — `/health` is a readiness check over critical dependencies

- **Decision**: A single `GET /health` endpoint pings Postgres, Redis, and Vault; it returns 200 with a
  per-dependency status map only when all are reachable, and a non-200 (503) when any critical dependency
  is down. The same endpoint is the Railway healthcheck.
- **Rationale**: FR-002/FR-003 and SC-002 (zero false-healthy) require dependency awareness; the
  clarification chose one endpoint over split liveness/readiness for simplicity (P1).
- **Alternatives considered**: Split liveness/readiness probes (rejected: speculative, P2); shallow
  200-only liveness (rejected: would report healthy while a dependency is down, violating SC-002).

## Decision 2 — Vault dev mode locally, secrets resolved at startup

- **Decision**: Run the official `hashicorp/vault` image in `-dev` mode (auto-unsealed, in-memory, fixed
  root token from `.env.example`). `scripts/seed_vault.sh` writes the app's secrets to a KV path on boot.
  The `hvac` adapter reads all secrets once at startup into an in-process settings object.
- **Rationale**: Satisfies one-command startup (SC-001) with no manual unseal, while still proving the
  Vault-only-secrets path (P6). Matches the spec clarification.
- **Alternatives considered**: Production-mode Vault with persisted storage + unseal step (rejected for
  local: breaks one-command startup; deferred to Phase 6 deployment).

## Decision 3 — Tracing via OpenTelemetry/OpenInference → self-hosted Phoenix

- **Decision**: Use `opentelemetry-sdk` + `openinference-instrumentation` + `arize-phoenix-otel` in the
  backend to export spans to a separate `arizephoenix/phoenix` container. Phoenix persists its trace data
  to the **same Postgres** instance via `PHOENIX_SQL_DATABASE_URL` (a dedicated schema/database). A custom
  span processor runs `core.redaction.redact` over span attributes before export.
- **Rationale**: Constitution mandates Phoenix self-hosted, OTel, traces to the same Postgres (P5/P10);
  the backend only carries the lightweight exporter, keeping the image small. FR-006/FR-007 require a
  trace per request with redaction before emit.
- **Alternatives considered**: Langfuse/SaaS tracing (rejected by constitution change log #5); separate
  trace datastore (rejected: reuse Postgres, P1/P10); redact-after-emit (rejected: violates P6/FR-007).

## Decision 4 — Redaction utility shared by logging and tracing

- **Decision**: `core/redaction.py` exposes pure functions (`redact(text)`, `redact_mapping(dict)`) with a
  documented interface. In this phase it is a deterministic stub (masks known secret keys/values and
  obvious token patterns); `structlog` calls it as a processor in the log pipeline and `infra/tracing.py`
  calls it in the span processor. Full Presidio-backed PII detection is wired in Phase 3 when untrusted
  cook input arrives.
- **Rationale**: Wiring both call sites now (the "two call sites" requirement) is cheap and prevents a
  later retrofit (FR-007); the stub keeps the image free of heavy NLP startup while the seam exists.
- **Alternatives considered**: Full Presidio now (rejected: P2 — no real PII paths yet; adds startup
  cost); redaction only in logging (rejected: traces must be redacted too, FR-007).

## Decision 5 — Dependency management & image leanness

- **Decision**: `uv` with a single `pyproject.toml`; the backend installs only the `backend` extra
  (`uv sync --frozen --no-dev --extra backend`). `dev`/`test` groups hold ruff, mypy, pytest. No `torch`
  anywhere. Image base `python:3.12-slim`, multi-stage with a cached dependency layer.
- **Rationale**: Matches `projectplanFolderForMd/dependencies.md` and P10 (lean images, no torch).
- **Alternatives considered**: pip/requirements.txt (rejected: constitution mandates uv); single fat
  dependency set (rejected: bloats image, widens attack surface).

## Decision 6 — Fail-fast startup & ordering

- **Decision**: The app validates required settings at construction (Pydantic) and verifies Vault is
  reachable + secrets resolvable during the FastAPI lifespan startup; a missing setting or unreachable
  Vault raises a clear error and aborts boot. Compose uses `depends_on` with healthchecks on
  postgres/redis/vault so the backend starts after they are healthy.
- **Rationale**: FR-010 + edge cases require fail-fast with actionable messages and no false-healthy
  startup ordering.
- **Alternatives considered**: Lazy secret resolution on first use (rejected: hides config errors until
  runtime); no compose healthchecks (rejected: backend could come up before deps, flaking the smoke test).

## Decision 7 — CI smoke test brings up the full stack

- **Decision**: GitHub Actions job spins up postgres(pgvector), redis, and vault-dev as service
  containers (Phoenix optional in CI; tracing export is best-effort and must not fail the request),
  installs the backend extra, runs Alembic baseline, boots the app, and asserts `/health` returns healthy.
  Separate fast jobs run `ruff` and `mypy`.
- **Rationale**: Spec clarification chose full-stack smoke to verify the real readiness contract (SC-005).
- **Alternatives considered**: App-only with mocks (rejected by clarification); compose-in-CI via
  docker-compose action (viable alternative; service containers chosen for speed and simplicity).

## Open follow-ups (non-blocking, later phases)

- Presidio-backed redaction rules (Phase 3, when cook input exists).
- Production-mode Vault config + seeding on Railway (Phase 6).
- Phoenix retention/cost dashboards (Phase 4 dashboard).
