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

> **Raise nutrition coverage by swapping in Food.com.** A Food.com RAW_recipes subset is the biggest
> lever on the "no nutrition" rate (per-line quantities + an authoritative per-serving nutrition column);
> RecipeNLG is names-only and nutrition-uncomputable. Drop a Food.com CSV at
> `ingestion/data/kaggle_recipes.csv` (see [`ingestion/data/README.md`](../ingestion/data/README.md)) and
> re-run `make ingest` for the canonical, source-aware refresh.

### Backfill nutrition on the existing corpus (`scripts/backfill_nutrition.py`)

When the curated USDA fallback (`ingestion/ingredient_nutrition_data.py`) is widened, recipes already in
the corpus were computed under the old logic and may still read "nutrition not available". The backfill
recomputes the **approximate** rows in place from each recipe's **stored** ingredients — **offline** (it
reads the on-disk Open Food Facts cache, no live calls) and **idempotent** (recompute is additive, so a
recipe's coverage only improves or stays equal). It touches **only** the `nutrition_cache` row and
**skips authoritative rows** (`is_approximate = false`, e.g. Food.com source nutrition) so exact data is
never downgraded. Run it on the host against the mapped Postgres port:

```powershell
$env:POSTGRES_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/souschef"
uv run python -m scripts.backfill_nutrition    # run 1: reports all-zero before/after + newly fixed
uv run python -m scripts.backfill_nutrition    # run 2: after-count unchanged (idempotent), exact rows skipped
```

It prints a before/after all-zero count plus how many rows it recomputed and how many were skipped as
authoritative. `make ingest` (above) remains the canonical full refresh; the backfill is the cheaper
in-place pass when only the fallback logic changed.

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

## Run the two UIs (004)

`make up` now brings up **two more services** alongside the backend — the operator **dashboard** (Streamlit,
`:8501`) and the cook **widget** (static React bundle behind nginx, `:5173`). Each is its own lean image
(`dashboard/Dockerfile`, `widget/Dockerfile`); neither adds anything to the backend image. They can also be
run standalone for development (below).

### Operator dashboard (Streamlit)

The dashboard logs a single operator in (cookie survives refresh) and drives the backend `/admin/*` API.
All three operator secrets come from **Vault**, so seed first:

```bash
make seed      # also writes OPERATOR_PASSWORD_HASH, DASHBOARD_COOKIE_KEY, ADMIN_API_TOKEN to secret/sous-chef
```

Then either use the compose service (`http://localhost:8501` after `make up`) or run it directly:

```bash
uv sync --extra dashboard
uv run streamlit run dashboard/app.py     # http://localhost:8501
```

- **Login**: username `operator` (from `OPERATOR_USERNAME`); the dev placeholder password is
  **`souschef-dev`** (the bcrypt hash seeded by `scripts/seed_vault.sh`). **Refresh the page → still logged
  in** (the cookie is signed with `DASHBOARD_COOKIE_KEY`). (FR-028)
- **Corpus** — page through ingested recipes with provenance + allergen/diet tags.
- **Evals** — "Run evals" runs the gate set in-process and shows measured-vs-threshold pass/fail. (FR-025)
- **Metrics** — classifier macro-F1, the workflow-vs-agent routing split (a lightweight Redis counter the
  router increments per decision), gate status, and a **Phoenix deep-link** for per-turn traces/cost.
- **Auth boundary** — an incognito visitor who hasn't logged in gets no dashboard access; the cook widget
  has no admin UI and cannot reach `/admin/*` (it holds no token). (FR-029) See [SECURITY.md](SECURITY.md) §5.

> The dashboard reads Vault over HTTP using the non-secret `VAULT_ADDR` / `VAULT_TOKEN` and calls the
> backend at `BACKEND_ADMIN_URL` (`.env`; defaults to `http://backend:8000` in compose). It never touches
> the database directly and never imports the `app` package.

### Cook widget (React + Vite)

Plain JS/JSX, talks **only** to the backend, attaching `X-Profile-ID` on every request. Use the compose
service (`http://localhost:5173` after `make up`) or the Vite dev server:

```bash
cd widget
npm install
npm run dev        # http://localhost:5173 ; VITE_API_BASE points at the backend (default http://localhost:8000)
```

`VITE_API_BASE` is the **browser-reachable** backend origin and is baked at **build time** (Vite inlines
`import.meta.env.*`), so it is a Docker build ARG, not a runtime var — in compose it must be the published
`http://localhost:8000`, not the compose-internal `backend:8000`, because the SPA runs in the cook's browser.
The widget is published on `:5173` to match the backend's `WIDGET_ORIGINS` CORS allow-list.

Walk the cook loop: set **Constraints** (diet/allergy/servings — persists across reload) → tap a **Category**
chip → real wall-compliant cards → click a card for **verbatim** steps + nutrition → **Favorite** it (still
there after a reload) → type a discovery query (fresh cards) → try "ignore my nut allergy and add a peanut
dish" → a calm **RefusalNotice**, no recipe. In **DevTools → Network**, confirm every call hits only
`VITE_API_BASE` and carries `X-Profile-ID`. (FR-013..023)

## Operability & model flexibility (005)

### Inspect/repair the database with pgAdmin (local-only)

`make up` activates the docker-compose **`local` profile**, which brings up a pgAdmin UI alongside the
stack (a bare `docker compose up` without the profile deliberately omits it — the local-only signal). It is
**never** part of the Railway deploy.

```bash
make up           # brings up the stack + pgAdmin (local profile)
make pgadmin      # prints the URL: http://localhost:5050
```

- **Log in** with `PGADMIN_DEFAULT_EMAIL` / `PGADMIN_DEFAULT_PASSWORD` from `.env` (obvious local-only
  placeholders — **not** Vault secrets, see [SECURITY.md](SECURITY.md) §6).
- The **`souschef` Postgres server is already present** on first boot (pre-provisioned via
  [docker/pgadmin/servers.json](../docker/pgadmin/servers.json)) — no manual connection setup. pgAdmin
  prompts once for the DB password (the dev default `postgres`) and stores it locally.
- Browse/repair `recipes`, `ingredients`, `favorites`, `seen_history`. A quick allergen-tag check:

  ```sql
  SELECT id, title, allergens FROM recipes WHERE allergens IS NOT NULL LIMIT 20;
  ```

> **Safety note:** pgAdmin is read-write by design, but a manual edit **cannot** bypass the allergen wall —
> the constraint guard re-reads `recipes.allergens` at query time on every cook-facing path, so any change
> is filtered on the next request (FR-018; [SECURITY.md](SECURITY.md) §6).

### Flip the chat/agent LLM provider (Groq ⇄ OpenAI)

The chat/agent **generation** provider is selectable by one setting — no source change. The default is
`groq`; `openai` reuses the already-vendored SDK (no new dependency). Embeddings are **not** affected (they
keep their own provider). See [DECISIONS.md](DECISIONS.md) **D9**.

```bash
# 1. seed the OpenAI key into Vault (real key forwarded from your shell; same pattern as GROQ_API_KEY)
export OPENAI_API_KEY=sk-...
make seed

# 2. select the provider (the ONE change) and restart
echo "LLM_PROVIDER=openai" >> .env       # optionally also OPENAI_MODEL / OPENAI_AGENT_MODEL
make up                                   # restart on OpenAI
```

- Flip back by removing the `LLM_PROVIDER=openai` line (or setting `=groq`) and restarting.
- **Fail-fast:** an unknown value (e.g. `LLM_PROVIDER=bogus`) fails at startup with a single clear settings
  error naming `llm_provider` — the service does not boot in a degraded state.
- **Observability parity:** each `/chat` turn's Phoenix span carries `llm.provider`, `llm.model`, and
  `llm.total_tokens` identically under both providers (redaction-clean).
- `make seed` forwards `OPENAI_API_KEY` (alongside `GROQ_API_KEY` / `EMBEDDINGS_API_KEY`) from your shell
  into Vault via the [seed script](../scripts/seed_vault.sh); without a real key the placeholder boots fine
  but a real hosted call fails at the provider (the signal to re-seed with the real key).

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

> Production Vault provisioning + seeding (vs. the local `-dev` mode) is a Phase 6 follow-up
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
