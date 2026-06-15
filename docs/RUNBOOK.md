# SousChef Runbook — v0.1.0

Operational notes for running and shipping SousChef: bring the local stack up, seed Vault, load the
committed seed corpus, view traces, deploy to Railway, and cut the release. The full reproduce/deploy/release
acceptance path is in [`specs/007-ship-public-deploy/quickstart.md`](../specs/007-ship-public-deploy/quickstart.md)
(the original foundation scenarios live in
[`specs/001-foundation/quickstart.md`](../specs/001-foundation/quickstart.md)).

**The end-to-end path at a glance** (each step is detailed below):

```
LOCAL:  make up  →  make seed  →  make load-seed                         → demo
DEPLOY: seed prod Vault (once) → unseal → alembic upgrade head (on boot)
        → load_seed_corpus.py (first deploy) → /health 200 promotes      → demo on live URL
RELEASE: rehearse demo on live URL + fresh-clone reproduce → tag v0.1.0
```

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

## Load the committed seed corpus (`make load-seed`) — the canonical data path

For local bring-up, a public deploy, and CI you do **not** run `make ingest` (which hits the source APIs).
Instead you load the **committed seed corpus** under [`seeds/corpus/`](../seeds/corpus/) — a pre-built,
categorized + embedded dataset (`recipes.jsonl` + `embeddings.npy` + `manifest.json`) — so local, CI, and
prod hold **byte-identical** data and the demo never hits a cold corpus (FR-013). The load is **network-free**
and **idempotent** (upsert on `source_id`) and makes **zero** provider calls. Contract:
[contracts/seed-corpus.md](../specs/007-ship-public-deploy/contracts/seed-corpus.md).

**Local — after `make up` + `make seed`:**

```bash
make load-seed     # runs `python -m scripts.load_seed_corpus` INSIDE the backend container
```

> Use `make load-seed`, **not** a host-side `uv run python scripts/load_seed_corpus.py`: `.env` points
> `POSTGRES_URL` at the docker hostname `postgres`, which only resolves on the compose network. The loader
> first validates `count` / `dim` / `manifest.embedding_model` against the **runtime** embeddings model and
> **fails fast** on any mismatch (so seeded vectors and live query vectors always share one space), then
> upserts rows + pgvector through the repo layer.

**Prod — first deploy only** (after the backend's `alembic upgrade head` has run and Vault is unsealed):

```bash
# from a workstation with the prod POSTGRES_URL + embeddings key available, or via `railway run`:
python -m scripts.load_seed_corpus
```

The committed artifact is regenerated offline (never in prod) by
[`scripts/export_seed_corpus.py`](../scripts/export_seed_corpus.py) against a populated dev DB; `embeddings.npy`
is tracked via **Git LFS** so it doesn't bloat the base repo.

**Rebuild the classifier (offline, never shipped):**

```bash
make train         # → ml/artifacts/model.joblib (TF-IDF + LogReg; no torch). CI rebuilds this fresh.
```

## View traces (Phoenix self-hosted, or LangSmith Cloud)

Each application request emits one redacted span (the tracing middleware in `app/main.py` →
`app/infra/tracing.py`). The destination is chosen by **`TRACING_PROVIDER`** (DECISIONS.md D11):
`phoenix` (default, local dev) or `langsmith` (LangSmith Cloud — used in prod where the host's service
cap leaves no room for a Phoenix service). Both export over OTLP/HTTP through the **same redacting
exporter**, so no secret reaches either sink (FR-007, golden rule #5). Export is best-effort: an
unreachable/misconfigured backend just runs the request untraced.

**Local (Phoenix, default):**

```bash
curl http://localhost:8000/health        # generates a request → a span
# open the Phoenix UI at http://localhost:6006 and confirm a corresponding trace appears
```

Phoenix persists its trace store in the same Postgres instance (`PHOENIX_SQL_DATABASE_URL`).

**Prod (LangSmith Cloud) — activation:** no Railway service needed.

1. Seed the real key into the prod Vault (it's a secret; never in env/image):
   ```bash
   VAULT_ADDR="$PROD_VAULT_ADDR" VAULT_TOKEN="$VAULT_ROOT_TOKEN" LANGSMITH_API_KEY="lsv2_..." sh scripts/seed_vault.sh
   ```
2. Flip the backend variables and redeploy:
   ```bash
   railway variables --service sous-chef --set TRACING_PROVIDER=langsmith --set LANGSMITH_PROJECT=souschef
   ```
3. Generate traffic and confirm traces appear in the LangSmith project. (`LANGSMITH_OTLP_ENDPOINT` defaults
   to `https://api.smith.langchain.com/otel`; override only for self-hosted LangSmith.) Vault must be
   unsealed at boot so the backend can read `LANGSMITH_API_KEY`.

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
| `REDIS_URL` | **Optional** — Railway Redis URL. Omit to run cache-less (see "Redis is optional…" below) |
| `TRACING_PROVIDER` | `phoenix` (default) or `langsmith` (prod, no extra service — D11) |
| `PHOENIX_COLLECTOR_ENDPOINT` | Phoenix collector base URL (only when `TRACING_PROVIDER=phoenix`; best-effort) |
| `LANGSMITH_PROJECT` | LangSmith project name (only when `TRACING_PROVIDER=langsmith`; non-secret). The `LANGSMITH_API_KEY` is a **Vault** secret, not a Railway var |

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

## Known deployment deviations (v0.1.0)

Reconciliation notes from the live `007-ship-public-deploy` bring-up — accepted deviations from the
contracts/docs, captured so an operator isn't surprised. None block the cook journey (live +
allergen-wall-verified). Each maps to a Phase 3b follow-up task in
[`specs/007-ship-public-deploy/tasks.md`](../specs/007-ship-public-deploy/tasks.md).

### Vault image pinned to `hashicorp/vault:1.13.3` (T017d)

The prod Vault service runs **`hashicorp/vault:1.13.3`**, a deliberate downgrade. The current 2.x image
runs as a non-root user (uid 100) and cannot write the root-owned Railway volume
(`mkdir /vault/data/core: permission denied`); 1.13.3 runs as root and writes the persistent volume
cleanly. **Revisit** when the upstream image/volume-ownership story improves (e.g. an init container that
`chown`s the volume, or a Railway volume mounted writable for uid 100), then unpin.

### Vault re-seals on every redeploy — manual unseal required (T017e)

Prod Vault is server-mode with **1 key share / threshold 1**, so it **seals on every redeploy** and the
operator must `vault operator unseal` (or via the public endpoint below) before the backend can read its
secrets and go healthy. This posture is accepted for v0.1.0, but it means **every Vault redeploy needs a
manual unseal** or the backend boots unhealthy.

> **Recommended fix:** configure **auto-unseal** with a cloud KMS (`seal "awskms"`/`gcpckms`/`transit`)
> so the backend boots unattended. Until then, after any Vault redeploy: unseal Vault first, then confirm
> `/health` → 200 on the backend.

### Vault has a public HTTPS endpoint (T017f)

Vault is reachable at a **public** Railway domain (`https://efficient-dream-production-e88d.up.railway.app`)
**only** for operator init/unseal/seed. It is sealed-by-default and root-token-gated, and the backend
reaches Vault over the **private** network — but a public ingress on Vault is a deviation from the
"no public ingress for Vault" topology in
[`contracts/`](../specs/007-ship-public-deploy/contracts/)/[`data-model.md`](../specs/007-ship-public-deploy/data-model.md).
**Remove the public domain once auto-unseal lands** (T017e), or accept + document it as here.

### Per-service Railway config-as-code gotcha (T017g)

A Railway service whose repo has a **root `railway.toml`** inherits it **unless** the service sets
`railwayConfigFile` to its own per-service file. During bring-up the `widget` service initially ran the
**backend's** `alembic` start command + `/health` gate until it was pointed at
[`railway/widget.toml`](../railway/widget.toml); it also needs `PORT=80` so Railway's networking targets
nginx. **Every non-backend service must set its own `railwayConfigFile`** — the `dashboard` and `phoenix`
services (T017b/T017c) must each point at [`railway/dashboard.toml`](../railway/dashboard.toml) /
[`railway/phoenix.toml`](../railway/phoenix.toml) to avoid the same trap.

### Backend/dashboard images exceed the ~500MB target (T039)

Golden rule #3 / [`docs/DESIGN.md`](DESIGN.md) aim for images **< ~500MB**. The two Python images land
**above** that and cannot reach it without dropping components the app genuinely needs:

| Image | Size | Driven by |
|-------|------|-----------|
| `backend` | **~1.27GB** | the backend venv is ~711MB: Presidio's PII stack (spaCy ~123M + `phonenumbers` ~46M + `blis` ~34M) plus the classifier-serving stack (scipy ~111M + scikit-learn ~49M + numpy ~42M). All required — Presidio is the redaction gate (golden rule #5) and scikit-learn serves the intent classifier. |
| `dashboard` | **~900MB** | streamlit + pandas + their transitive numeric stack. |
| `widget` | ~74MB | nginx + the built static bundle — well under target. |

Still **no torch** in any image (golden rule #3's hard line holds) and no dev/test tooling
(`uv sync --no-dev`). The Dockerfiles keep the uv **wheel cache** out of the image via a BuildKit cache
mount (`--mount=type=cache,target=/root/.cache/uv`), which already cut the images ~36%/38% (backend
1.99GB→1.27GB, dashboard 1.44GB→900MB) — the leftover size is the resolved venv itself, not cache.

**Accepted for v0.1.0** — the images run fine on Railway and nothing in the cook journey depends on the
~500MB figure. **Revisit** only if image pull/boot time becomes a problem: options are a multi-stage build
that strips spaCy model build artifacts, or moving classifier serving / PII redaction behind a smaller
runtime. Until then the ~500MB line in DESIGN.md is aspirational, noted here so an operator isn't surprised.

## Redis is optional — remove it to free a Railway service slot

Redis has **one** product use: the operator dashboard's workflow-vs-agent **routing-split counter**
(`router.record_decision` increments `routing:agent`/`routing:workflow`; the dashboard's metrics page reads
them). That path is already **best-effort** — it no-ops when the cache is absent — and Redis is used for
nothing else (no caching, sessions, freshness/seen-history [Postgres], retrieval, the agent, or the wall).

As of this change `REDIS_URL` is **optional** ([`app/config.py`](../app/config.py)): when it is unset the
app builds **no cache**, and `/health` **omits** the redis check instead of 503-ing on it
([`app/main.py`](../app/main.py), [`app/api/health.py`](../app/api/health.py)). So on a capacity-limited
plan (e.g. Railway's free/trial 5-service cap) the `Redis` service can be deleted to free a slot — e.g. for
the operator `dashboard` — with **zero effect on the cook journey**. The only visible loss is the
dashboard's routing-split metric, which then reads an honest empty `0% / 0% / 0 turns`.

**Procedure (Railway):**

1. Deploy this code first (so the backend can run cache-less): merge to `main`.
2. **Remove the `REDIS_URL` variable** from the `sous-chef` service (Variables → delete `REDIS_URL`).
   This redeploys the backend; `/health` now reports only `postgres` + `vault` and stays `200`.
   ```bash
   railway variables --service sous-chef --remove REDIS_URL   # or delete it in the dashboard UI
   ```
3. Verify health is green without redis:
   ```bash
   curl -s https://<backend>.up.railway.app/health   # dependencies = {postgres, vault}, status "ok"
   ```
4. **Delete the `Redis` service** (Service → Settings → Delete). The slot is now free for `dashboard`.

> Local dev is unchanged: `docker-compose` still runs Redis and `make up` still sets `REDIS_URL`, so
> `/health` reports redis and the routing-split metric works locally. Reversible — re-add `REDIS_URL`
> (and a Redis service) to restore it.

## Cut the release (`v0.1.0`)

The release gate is a **rehearsal on both surfaces** before any tag (quickstart §F, SC-007):

1. **Demo on the live URL** — open the widget at `https://widget-production-5547.up.railway.app`, run the
   full scenario (chat → cards → verbatim steps → meal plan → shopping list → favorite) with an
   allergy/diet constraint; confirm **zero** wall/grounding violations and a valid HTTPS certificate.
2. **Reproduce on a fresh clone** — on a clean checkout: `make up` → `make seed` → `make load-seed`, then
   run the same demo locally and confirm identical safety behavior (SC-006).
3. **Tag the exact live + reproducible commit** and push it:

   ```bash
   git tag -a v0.1.0 -m "SousChef v0.1.0 — first public release"
   git push origin v0.1.0
   ```

   The tag must point at the commit that is **both** running at the public URL **and** reproducible locally
   — i.e. the green `main` Railway last auto-deployed (`git log origin/main -1`).

## Failure recovery

Quick triage for the failure modes seen during bring-up. Deviations behind these are detailed in *Known
deployment deviations* above.

| Symptom | Likely cause | Recovery |
|---|---|---|
| Backend `/health` 503 after a **Vault** redeploy | Vault re-sealed (1 share / threshold 1 — T017e) | `vault operator unseal` (or via the public operator endpoint), then re-check `/health`. Consider auto-unseal (cloud KMS). |
| Backend won't boot: "could not load secrets" / seed-pointing `StartupConfigError` | prod Vault path `secret/sous-chef` not seeded, or a required key missing | run `scripts/seed_vault.sh` against the prod `VAULT_ADDR`/`VAULT_TOKEN` with real keys exported (FR-014). |
| A non-backend service runs the backend's `alembic`/`/health` start command | service inherited the root `railway.toml` (T017g) | set that service's `railwayConfigFile` to its own `railway/<svc>.toml` (and `PORT=80` for the nginx widget). |
| Vault container: `mkdir /vault/data/core: permission denied` | the 2.x image runs as non-root vs the root-owned Railway volume (T017d) | the prod Vault is pinned to `hashicorp/vault:1.13.3` (runs as root); keep the pin until the volume-ownership story improves. |
| `load_seed_corpus.py` fails fast on a model/dim mismatch | the committed corpus was embedded with a different model than the runtime `EMBEDDINGS` model | align the runtime embeddings model to `manifest.embedding_model`, or re-export the corpus — **never** bypass the check (seeded and query vectors must share one space). |
| `make load-seed` host-side: cannot resolve `postgres` | ran the loader on the host, where `.env`'s `POSTGRES_URL` points at the compose hostname | use `make load-seed` (runs inside the backend container). |
| CI `gates` job red on red-team/redaction | a real safety regression (or an intentionally-added failing probe) | **fix the cause** — never weaken a threshold (golden rule #6). Revert the offending change; the merge is correctly blocked. |
| Tracing shows nothing in prod | `TRACING_PROVIDER`/key not set | tracing is **non-blocking** (`/health` unaffected); to enable, seed `LANGSMITH_API_KEY` in Vault + set `TRACING_PROVIDER=langsmith` (see *View traces* above). |
