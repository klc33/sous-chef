# Quickstart & Validation: Foundation

This guide proves the foundation works end-to-end. It maps each scenario to the spec's acceptance
criteria. It is a run/validation guide — implementation details live in `tasks.md` and the code.

## Prerequisites

- Docker + Docker Compose (a container engine that can run multiple services).
- `uv` installed (for local non-container runs and tests).
- A fresh clone of the repo on `001-foundation`. No manual file edits required.

## Scenario 1 — One-command startup (US1 / SC-001 / SC-006)

```bash
make up        # docker-compose up: backend + postgres(pgvector) + redis + vault(dev) + phoenix
```

Expected:
- All five services start; postgres, redis, and vault report healthy before the backend starts.
- `scripts/seed_vault.sh` seeds dev Vault secrets automatically — no manual unseal/seed step.
- The backend reaches a running state.
- Re-running `make down && make up` returns the stack to healthy without manual repair.

## Scenario 2 — Readiness health endpoint (US2 / SC-002)

```bash
curl -i http://localhost:8000/health
```

Expected (healthy): HTTP `200` with a JSON body matching
[`contracts/health.openapi.yaml`](contracts/health.openapi.yaml), e.g.
`{"status":"ok","dependencies":{"postgres":"ok","redis":"ok","vault":"ok"}}`, returned in under 1 second.

Degraded check:

```bash
docker compose stop redis
curl -i http://localhost:8000/health     # expect HTTP 503, redis: "unreachable", no false-healthy
docker compose start redis
```

## Scenario 3 — Secrets never leak (US3 / SC-003)

```bash
docker compose logs backend | grep -i -E "token|secret|password|key" || echo "no secret-looking values"
```

Expected:
- No real secret value appears in logs.
- Inspecting Phoenix traces shows no secret values in span attributes (redaction runs before export).
- `.env.example` contains only non-secret bootstrap values (Vault addr/token + service URLs), confirmed
  by review.

## Scenario 4 — A trace per request (US4 / SC-004)

```bash
curl http://localhost:8000/health        # generate a request
# open Phoenix UI (default http://localhost:6006) and confirm a corresponding trace appears
```

Expected: each application request produces a corresponding (redacted) trace in Phoenix.

## Scenario 5 — CI gates & deploy (US4 / SC-005)

Locally mirror CI:

```bash
make lint                                  # ruff + mypy, clean
uv run pytest tests/integration/test_health_smoke.py   # boots app vs stack, asserts /health healthy
```

Expected:
- `ruff` and `mypy` pass.
- The smoke test stands up the backing services and asserts `/health` reports healthy.
- On green `main`, the Railway build deploys and its `/health` healthcheck passes at the public URL.

## Migration sanity (data-model baseline)

```bash
make up
docker compose exec backend uv run alembic upgrade head
docker compose exec postgres psql -U postgres -c "SELECT extname FROM pg_extension WHERE extname='vector';"
```

Expected: the `vector` extension is present (baseline migration `0001_baseline` applied).

## Done / acceptance mapping

| Scenario | Spec criteria |
|---|---|
| 1 | SC-001, SC-006, FR-001 |
| 2 | SC-002, FR-002, FR-003 |
| 3 | SC-003, FR-004, FR-005, FR-005a, FR-007 |
| 4 | SC-004, FR-006 |
| 5 | SC-005, FR-008, FR-009 |
| Migration | FR-011 |
