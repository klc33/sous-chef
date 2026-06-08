# SousChef Runbook — Foundation

Operational notes for the foundation skeleton: bring the local stack up, seed Vault, view traces in
Phoenix, and deploy to Railway. Everything here maps to the quickstart scenarios
([`specs/001-foundation/quickstart.md`](../specs/001-foundation/quickstart.md)).

## Bring the stack up (`make up`)

One command brings up all five services — backend + Postgres(pgvector) + Redis + Vault(dev) +
Phoenix (US1 / SC-001):

```bash
make up        # copies .env from .env.example if missing, then `docker compose up --build`
```

> **Windows (no `make`):** the Makefile is GNU-make/Unix-shell and won't run under PowerShell. Make
> sure Docker Desktop is running, then run the equivalent directly:
> ```powershell
> if (-not (Test-Path .env)) { Copy-Item .env.example .env }
> docker compose up --build        # add -d to run detached
> ```
> (Or install make via `winget install GnuWin32.Make` / `choco install make`.)

What happens:

- Postgres, Redis, and Vault must report **healthy** (compose healthchecks) before the backend
  starts — no false-healthy startup ordering.
- The backend then runs `alembic upgrade head` → `scripts/seed_vault.sh` → `uvicorn`. Seeding
  precedes uvicorn because the app loads its secrets from Vault at startup and fails fast if absent.
- Tear down and return to a clean slate with `make down` (removes volumes); `make up` again returns
  to healthy.

Verify readiness once it is up (US2 / SC-002):

```bash
curl -i http://localhost:8000/health     # expect HTTP 200 + {"status":"ok", ...}
docker compose stop redis
curl -i http://localhost:8000/health     # expect HTTP 503 + redis "unreachable" (no false-healthy)
docker compose start redis
```

## Seed Vault secrets

Compose seeds dev Vault automatically on backend boot. To re-seed the running stack manually
(idempotent — a KV v2 write overwrites the path):

```bash
make seed      # docker compose exec backend sh scripts/seed_vault.sh
```

Dev secrets live at KV v2 mount `secret`, path `sous-chef` (what `app/infra/vault.py` reads). The
seeded values are **throwaway dev placeholders** — real provider keys are added in their own phases
and never committed (golden rule #4).

## View traces in Phoenix

Each application request emits one redacted span (the tracing middleware in `app/main.py` →
`app/infra/tracing.py`). Generate a request and inspect it (US4 / SC-004):

```bash
curl http://localhost:8000/health        # generates a request → a span
# open the Phoenix UI at http://localhost:6006 and confirm a corresponding trace appears
```

Redaction runs over span attributes **before** export, so no secret value reaches Phoenix
(FR-007). Phoenix persists its trace store in the same Postgres instance
(`PHOENIX_SQL_DATABASE_URL`). Tracing export is best-effort: if Phoenix is unreachable the request
still succeeds untraced.

## Confirm no secret reaches the logs (SC-003)

```bash
docker compose logs backend | grep -i -E "token|secret|password|key" || echo "no secret-looking values"
```

The structlog pipeline routes every event through `core.redaction.redact` before a line is written;
`.env.example` holds only non-secret bootstrap values (real secrets live in Vault).

## Deploy target (Railway)

The backend deploys to Railway from the repo `Dockerfile`, gated on the `/health` readiness
endpoint. Build, start command, and the healthcheck path are declared in
[`railway.toml`](../railway.toml):

- **Build**: Dockerfile (`uv sync --frozen --no-dev --extra backend`; no torch; small image).
- **Start**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT` (`$PORT` injected by Railway).
- **Healthcheck**: `GET /health` — a deployment is promoted only when it returns `200` (Postgres,
  Redis, and Vault all reachable). A `503` holds the rollout (no false-healthy).

### Required service variables (set in the Railway service, NOT in the repo)

`app/config.py` requires these non-secret bootstrap values; provide Railway-managed Postgres/Redis
and a reachable Vault. **Real secrets live in Vault, never in Railway variables or the image.**

| Variable | Notes |
|---|---|
| `ENV` | `production` |
| `VAULT_ADDR` | URL of the production Vault (not dev mode) |
| `VAULT_TOKEN` | injected by the platform; never committed |
| `POSTGRES_URL` | Railway Postgres connection URL (`postgresql+psycopg://…`) |
| `REDIS_URL` | Railway Redis connection URL |
| `PHOENIX_COLLECTOR_ENDPOINT` | Phoenix collector base URL (tracing is best-effort) |

> Production Vault provisioning + seeding (vs. the local `-dev` mode) is a Phase 5 follow-up
> tracked in `research.md`; until then point `VAULT_ADDR`/`VAULT_TOKEN` at a reachable Vault whose
> `secret/sous-chef` path is seeded the same way as [`scripts/seed_vault.sh`](../scripts/seed_vault.sh).

## Connect & verify the deploy (manual, one-time — operator action)

These steps require the Railway dashboard/CLI and a GitHub connection; they cannot be performed
from the repo and must be done by an operator with account access:

1. **Link the repo**: in Railway, create a service from this GitHub repo and enable auto-deploy on
   `main`. Railway picks up [`railway.toml`](../railway.toml) automatically.
2. **Add the service variables** from the table above; attach Railway Postgres + Redis plugins.
3. **Trigger a deploy** by merging a green `main` (CI must pass: ruff + mypy + smoke).
4. **Verify** the deployment goes healthy: Railway's `/health` check must pass. Then from a
   workstation:

   ```bash
   curl -i https://<your-service>.up.railway.app/health    # expect HTTP 200 + status "ok"
   ```

5. **Record the public URL** here once verified:

   - Public URL: `__________________________` _(fill in after first successful deploy)_
   - First verified: `____-__-__`
   - `/health` at the public URL returned: `200 ok` ☐

> **Status:** `railway.toml` + CI are in place. The live Railway connection and public-URL
> verification (step 1–5) are an external operator action and are **not yet done** — complete them
> to close FR-008 / SC-005, then fill in the URL above.
