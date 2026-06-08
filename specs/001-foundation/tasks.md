                                                                                                        ---
description: "Task list for 001-foundation implementation"
---

# Tasks: Foundation — Runnable, Reproducible, Secure Skeleton

**Input**: Design documents from `specs/001-foundation/`

**Prerequisites**: [plan.md](plan.md) (required), [spec.md](spec.md) (user stories), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/)

**Tests**: Included — the spec mandates a full-stack smoke test (FR-009) and the constitution gates a redaction test (P4/P6). Other test tasks are kept minimal for this skeleton.

**Organization**: Tasks are grouped by user story (US1–US4 from spec.md) in priority order. The monolith tree already exists as empty placeholders (`projectplanFolderForMd/structure.md`); tasks **fill** files, they do not create the tree.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US4; Setup/Foundational/Polish carry no story label
- Exact file paths are given in each task

## Path Conventions

Single FastAPI monolith at repo root: `app/`, `alembic/`, `tests/`, plus root infra/ops files. Paths below are repo-relative.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: uv dependency groups, lockfile, and tooling — the base every later task needs.

- [X] T001 Fill `pyproject.toml` with the uv layout from `projectplanFolderForMd/dependencies.md`: shared base (`pydantic`, `pydantic-settings`, `httpx`, `structlog`) + `backend` extra (fastapi, uvicorn[standard], sqlalchemy, alembic, psycopg[binary], pgvector, redis, hvac, opentelemetry-sdk, openinference-instrumentation, arize-phoenix-otel, presidio-analyzer, presidio-anonymizer) + `dev`=[ruff, mypy], `test`=[pytest, pytest-asyncio, httpx]. NO torch. `requires-python>=3.11`.
- [X] T002 Generate `uv.lock` by running `uv sync --extra backend` (pins all deps reproducibly).
- [X] T003 [P] Configure `ruff` + `mypy` sections in `pyproject.toml` (target py312, sensible strictness for the app package).
- [X] T004 [P] Fill `.env.example` with ONLY non-secret bootstrap vars: `ENV`, `VAULT_ADDR`, `VAULT_TOKEN` (dev token), `POSTGRES_URL`, `REDIS_URL`, `PHOENIX_COLLECTOR_ENDPOINT`. No real secrets.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: An app that boots and can reach its dependencies — required before any user story.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T005 Implement Pydantic settings in `app/config.py`: typed non-secret fields from env, fail-fast validation on missing required values (FR-010). Add a docstring per function explaining behavior.
- [X] T006 [P] Implement error types + FastAPI exception handlers in `app/core/errors.py` (clean 4xx/5xx; a startup-config error type for fail-fast).
- [X] T007 [P] Implement `app/core/redaction.py` stub: `redact(text)` and `redact_mapping(mapping)` that mask known secret keys/values and obvious token patterns; documented interface (Presidio wiring deferred). Comment each function.
- [X] T008 Implement structlog configuration in `app/core/logging.py` with `redaction` as a processor in the chain (depends on T007).
- [X] T009 Implement the Vault adapter in `app/infra/vault.py` (hvac): connect using settings, load all app secrets from a KV path at startup into memory, expose `get(key)` and a `ping()`/reachability check; never log secret values (depends on T005).
- [X] T010 [P] Implement SQLAlchemy engine + session factory + `ping()` in `app/infra/db.py` (depends on T005).
- [X] T011 [P] Implement Redis client + `ping()` in `app/infra/cache.py` (depends on T005).
- [X] T012 Implement the FastAPI app factory in `app/main.py`: build settings → init logging → connect Vault & load secrets → lifespan that opens/closes db & cache → return app. Fail-fast on startup errors (depends on T005, T008, T009, T010, T011).
- [X] T013 Configure Alembic: `alembic.ini` (repo root) + `alembic/env.py` pointed at `app.models` metadata, DB URL sourced from settings/Vault at runtime (depends on T005).
- [X] T014 Create baseline migration `alembic/versions/0001_baseline.py`: `CREATE EXTENSION IF NOT EXISTS vector;` and nothing else (depends on T013).

**Checkpoint**: The app constructs, validates config, connects to Vault/Postgres/Redis, and Alembic can upgrade to a `vector`-enabled baseline. User stories can begin.

---

## Phase 3: User Story 1 — One-command local startup (Priority: P1) 🎯 MVP

**Goal**: A fresh clone brings up backend + postgres(pgvector) + redis + vault(dev) + phoenix with one command, with Vault auto-seeded — no manual steps.

**Independent Test**: On a clean checkout, `make up` brings all five services to a healthy/running state with no manual edits; `make down && make up` returns cleanly to healthy.

### Implementation for User Story 1

- [X] T015 [US1] Write the backend `Dockerfile` (repo root): `python:3.12-slim`, copy uv binary, multi-stage cached dependency layer via `uv sync --frozen --no-dev --extra backend`, copy `app/` + `alembic/` + `alembic.ini`, run uvicorn. Image stays small; no torch.
- [X] T016 [US1] Author `scripts/seed_vault.sh`: write the app's dev secrets to the Vault KV path used by `app/infra/vault.py` (idempotent; safe to re-run on boot).
- [X] T017 [US1] Write `docker-compose.yml` with five services — `backend` (build `.`, runs alembic upgrade + seed_vault + uvicorn, `depends_on` healthy deps), `postgres` (pgvector image, healthcheck), `redis` (healthcheck), `vault` (dev mode, fixed root token, healthcheck), `phoenix` (arizephoenix/phoenix, `PHOENIX_SQL_DATABASE_URL` → the same Postgres). Backend env from `.env`.
- [X] T018 [US1] Add Makefile targets `up` (compose up), `down` (compose down -v), `seed` (run seed_vault) so startup is one command (depends on T017).
- [X] T019 [US1] Add a Quickstart section to `README.md` documenting the single `make up` command and expected healthy state.

**Checkpoint**: `make up` from a fresh clone yields a fully running stack — MVP is demonstrable.

---

## Phase 4: User Story 2 — Readiness health endpoint (Priority: P1)

**Goal**: A single `GET /health` readiness endpoint reflecting Postgres/Redis/Vault reachability, used by operators and the deploy platform, verified by a full-stack smoke test.

**Independent Test**: With the stack up, `curl /health` returns 200 + dependency map; stopping a dependency yields 503 with that dependency `unreachable` (no false-healthy).

### Tests for User Story 2 ⚠️

- [X] T020 [P] [US2] Create `tests/conftest.py` fixtures: app client (httpx ASGI), settings override for tests.
- [X] T021 [US2] Write `tests/integration/test_health_smoke.py`: boot the app against the running stack and assert (a) the healthy path — `/health` returns 200 + `status: ok` with the shape in `contracts/health.openapi.yaml`; and (b) the degraded path — with a critical dependency made unreachable, `/health` returns 503 + that dependency `unreachable` and `status: unhealthy` (the no-false-healthy guarantee, SC-002). Depends on T020. Write the test before T022–T023 and confirm it fails first.

### Implementation for User Story 2

- [X] T022 [US2] Implement `GET /health` in `app/api/health.py`: call `db.ping()`, `cache.ping()`, `vault.ping()`; return 200 + per-dependency map when all reachable, 503 when any is `unreachable`, per `contracts/health.openapi.yaml`. Comment the function.
- [X] T023 [US2] Register the health router in `app/main.py` app factory (depends on T012, T022).

**Checkpoint**: `/health` reflects real dependency state; smoke test passes against the stack.

---

## Phase 5: User Story 3 — Secrets only from Vault, never leaked (Priority: P1)

**Goal**: Every secret resolves from Vault at runtime; nothing secret lives in code/config/images; redaction keeps secrets out of logs (and, with US4, traces).

**Independent Test**: Exercise a secret-backed path, grep `backend` logs → no secret values; review `.env.example` → only non-secret bootstrap values.

### Tests for User Story 3 ⚠️

- [X] T024 [P] [US3] Write `tests/unit/test_redaction.py`: feed fake secrets/tokens through `redact`/`redact_mapping` and assert they never appear in cleartext output (the redaction gate).

### Implementation for User Story 3

- [X] T025 [US3] Verify/extend `app/infra/vault.py` so ALL app secrets are sourced from Vault only (no env/code fallback); a missing secret raises a clear error rather than silently defaulting (FR-004/FR-010).
- [X] T026 [US3] Confirm `.env.example` (T004) holds only non-secret bootstrap values and document, in `README.md`, that real secrets live in Vault (FR-005).
- [X] T027 [US3] Ensure the structlog pipeline (T008) routes all log events through `redact` so secret-bearing fields are masked before any line is written (FR-007 logging half).

**Checkpoint**: No secret value reaches logs; secrets come exclusively from Vault.

---

## Phase 6: User Story 4 — Trace per request + public deploy (Priority: P2)

**Goal**: Each application request produces a redacted trace in Phoenix; a build deploys to a public HTTPS URL gated on `/health`; CI runs lint + type-check + full-stack smoke.

**Independent Test**: Send a request → a corresponding trace appears in Phoenix with no secret values; CI is green; the deployed `/health` passes at the public URL.

### Implementation for User Story 4

- [X] T028 [US4] Implement `app/infra/tracing.py`: OpenTelemetry/OpenInference setup exporting to Phoenix (`arize-phoenix-otel`); add a span processor that runs `core.redaction.redact` over span attributes before export (FR-007 trace half) (depends on T007).
- [X] T029 [US4] Wire tracing init + a request middleware into the `app/main.py` factory so every application request emits a span; tracing export failures must not fail the request (depends on T012, T028).
- [X] T030 [P] [US4] Write `railway.toml`: build via Dockerfile, start command, and `/health` as the healthcheck path.
- [X] T031 [P] [US4] Fill `eval_thresholds.yaml` with placeholder gate keys (classifier F1, RAG hit@k, redteam, redaction, smoke) commented as "set in later phases".
- [X] T032 [US4] Write `.github/workflows/ci.yml`: a `ruff` job, a `mypy` job, and a smoke job that starts postgres(pgvector)+redis+vault(dev) service containers, installs the backend extra, runs `alembic upgrade head`, boots the app, and runs `tests/integration/test_health_smoke.py` (depends on T021).
- [~] T033 [US4] Connect and verify the deploy (FR-008/SC-005): wire the Railway service to the GitHub repo so a green `main` auto-deploys using `railway.toml` (build + start + `/health` healthcheck), then confirm the deployed service is reachable at its public HTTPS URL and its `/health` check passes. Record the URL and the verification step in `docs/RUNBOOK.md` (depends on T030, T032). **Repo artifacts done (`railway.toml`, `docs/RUNBOOK.md` procedure); the live Railway connection + public-URL verification is an external operator action — see RUNBOOK "Connect & verify the deploy".**

**Checkpoint**: Requests are traced (redacted), CI gates the change, and a green `main` deploys to a public Railway URL whose `/health` passes.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Documentation and final validation across the skeleton.

- [X] T034 [P] Write the foundation section of `docs/RUNBOOK.md`: compose up, seed Vault, view Phoenix, deploy to Railway.
- [X] T035 Run `make lint` (ruff + mypy) and fix any findings across the new files. **Clean: `ruff check app alembic` → all passed; `mypy app` → no issues (72 files).**
- [~] T036 Run all `quickstart.md` scenarios end-to-end against `make up` and confirm each acceptance mapping passes (SC-001…SC-006). **Non-Docker subset PASS: Scenario 5 lint (ruff+mypy) clean and the smoke test (`tests/integration/test_health_smoke.py`, SC-002) green; full suite 13 passed. Scenarios 1–4 + migration sanity require a running Docker daemon (unreachable in this environment) — operator action: run `make up` then the quickstart curl/grep/Phoenix checks.**

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (P1)**: no dependencies — start immediately.
- **Foundational (P2)**: depends on Setup — BLOCKS all user stories.
- **User Stories (P3–P6)**: all depend on Foundational. In priority order P1→P1→P1→P2. Note real couplings below (this is one solo build, so stories are sequenced, not parallelized).
- **Polish (P7)**: depends on all stories being complete.

### User Story Dependencies & couplings

- **US1 (startup)**: needs Foundational + the backend `Dockerfile` (T015) to build the compose `backend` service. The MVP.
- **US2 (/health)**: needs Foundational; its smoke test (T021) and degraded check are best run against the US1 stack.
- **US3 (secrets)**: needs Foundational (Vault adapter, redaction/logging). Largely independent; redaction unit test (T024) needs no stack.
- **US4 (tracing/deploy)**: needs Foundational; the Phoenix service comes from US1's compose; CI smoke (T032) reuses US2's test (T021).

### Within each phase

- Tests before the implementation they cover (T021 before T022–T023; T024 before/with T027).
- Adapters (db/cache/vault) before the app factory; app factory before routers/middleware.
- Commit after each task or logical group.

### Parallel Opportunities

- Setup: T003, T004 in parallel.
- Foundational: T006, T007 in parallel; then T010, T011 in parallel (both after T005).
- US4: T030, T031 in parallel.
- Cross-story: a second contributor could take US3 (T024–T027) alongside US1/US2 since it shares only the already-built Vault/redaction foundation.

---

## Parallel Example: Foundational adapters

```bash
# After T005 (config) is done, these touch different files and can run together:
Task: "Implement app/core/errors.py"        # T006
Task: "Implement app/core/redaction.py"      # T007
Task: "Implement app/infra/db.py ping()"     # T010
Task: "Implement app/infra/cache.py ping()"  # T011
```

---

## Implementation Strategy

### MVP First (User Story 1)

1. Phase 1 Setup → 2. Phase 2 Foundational → 3. Phase 3 US1 → **STOP & VALIDATE**: `make up` from a fresh clone brings the stack healthy. Demoable MVP.

### Incremental Delivery

1. Setup + Foundational → app boots & connects.
2. US1 → one-command stack (MVP).
3. US2 → `/health` readiness + smoke test.
4. US3 → secrets-from-Vault + redaction proven.
5. US4 → tracing + CI + Railway deploy.
6. Polish → docs + full quickstart validation.

---

## Notes

- [P] = different files, no incomplete-task dependency.
- The directory tree already exists; tasks fill empty placeholder files — do not recreate the tree.
- Every function gets a comment explaining how it works (project preference).
- No product/business logic, no torch, secrets only in Vault — enforced throughout (constitution P2/P6/P10).
- Total: 36 tasks (T001–T036).
