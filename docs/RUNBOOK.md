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

## Build the recipe corpus (`make ingest`)

Offline, idempotent pipeline that populates the catalog (feature `002-catalog-wall-favorites`):
fetch → categorize → extract ingredients → allergens + nutrition → load → coverage report. It runs
against the configured Postgres (the same `POSTGRES_URL` the stack uses) and never ships in any image
(no torch; the `ingestion` dependency group is offline-only).

```bash
uv sync --group ingestion      # one-time: installs pandas (offline group)
make ingest                    # → uv run python -m ingestion.run_ingest
```

Sources:

- **TheMealDB** (food) and **TheCocktailDB** (non-alcoholic drinks) — pulled live from their free APIs;
  no key or local file needed.
- **Kaggle subset** (volume) — *optional*. Place a CSV at **`ingestion/data/kaggle_recipes.csv`**
  (gitignored; see [`ingestion/data/README.md`](../ingestion/data/README.md)). Either **RecipeNLG** or
  **Food.com (RAW_recipes)** works — `fetch_kaggle.py` normalizes whichever columns are present. Take a
  modest subset (~1,500 rows); the corpus target is a few hundred to ~2,000 recipes total. **No file? The
  run still succeeds on the two APIs alone.**

> **Nutrition accuracy:** when the **Food.com** `nutrition` column is present (a per-serving
> `[calories, total-fat PDV, sugar PDV, sodium PDV, protein PDV, sat-fat PDV, carbs PDV]` list), ingestion
> uses it as **authoritative** (`is_approximate = false`). Sources without it (RecipeNLG / TheMealDB /
> TheCocktailDB) fall back to an Open Food Facts estimate flagged `is_approximate = true`. Keep that
> column in your Food.com subset for exact macros.

Re-running is safe — every recipe upserts on `(source, source_id)`, so the corpus converges without
duplicates. The run ends with a coverage report (per-category counts, `% allergen_certain`, and the
surfaceable count for a representative allergic profile).

Corpus sanity — every surfaceable recipe is complete (SC-002), expect **0 rows**:

```sql
SELECT id FROM recipes
WHERE is_complete = true
  AND (category IS NULL
       OR id NOT IN (SELECT recipe_id FROM ingredients)
       OR id NOT IN (SELECT recipe_id FROM nutrition_cache));
```

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

   - Public URL: `https://sous-chef-production-721e.up.railway.app`
   - First verified: `2026-06-08`
   - `/health` at the public URL returned: `200 ok` ☑

> **Status:** ✅ Deployed and verified. The `sous-chef` backend is live on Railway behind
> Postgres (pgvector), Redis, and a dev-mode Vault service; `/health` returns `200` with all
> dependencies `ok` at the public URL above. **FR-008 / SC-005 closed.** (Tracing runs only where a
> Phoenix collector is configured; this deploy runs untraced by design — see the tracing note.)
