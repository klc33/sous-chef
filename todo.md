# TODO — Deploy SousChef backend to Railway

Step-by-step to take the foundation backend live on a public Railway URL gated on `/health`
(closes **FR-008 / SC-005**, task **T033**). Work top to bottom; tick each box as you go.

> **Already done for you (code):** [railway.toml](railway.toml) `startCommand` now runs
> `alembic upgrade head` → `seed_vault.sh` → `uvicorn`, so the deploy migrates + seeds itself.
> Everything below is dashboard work that needs your Railway account.

---

## 0. Prerequisites

- [X] Railway account: https://railway.app (sign in with GitHub).
- [X] Repo pushed to GitHub. Railway auto-deploys the **`main`** branch, so your foundation work
      must be merged to `main` before the first real deploy. (You can deploy a branch manually
      while testing.)
- [X] CI is green on the branch you'll deploy (ruff + mypy + smoke).

---

## 1. Create the project and link GitHub

- [X] Railway → **New Project** → **Deploy from GitHub repo** → select your `sous-chef` repo.
- [X] Open the created **backend service** → **Settings → Build**: confirm builder is **Dockerfile**
      (auto-detected from [railway.toml](railway.toml); no manual config needed).
- [X] **Settings → Deploy**: confirm **Auto Deploy** is ON and the branch is **`main`**.

---

## 2. Add PostgreSQL (must have pgvector)

- [X] Project canvas → **New → Database → Add PostgreSQL**.
- [X] Open the Postgres service → **Data / Query** tab → run:
      `CREATE EXTENSION IF NOT EXISTS vector;`
  - ✅ Succeeds → good.
  - ❌ Errors (`extension "vector" is not available`) → the default image lacks pgvector. Delete it
        and instead add Railway's **pgvector template** (search templates for "pgvector"), or the
        baseline migration will fail at deploy.
- [X] Note: you do **not** need to create tables — the deploy's `alembic upgrade head` does that.

---

## 3. Add Redis

- [X] Project canvas → **New → Database → Add Redis**.
- [X] No further config; you'll reference its URL in Step 5.

---

## 4. Add a Vault service (dev mode, for the demo)

The app loads secrets from Vault at startup and **fails fast if Vault is unreachable**. Simplest
working option for a demo:

- [X] Project canvas → **New → Empty Service**.
- [X] **Settings → Source → Image**: `hashicorp/vault:latest`.
- [X] **Settings → Deploy → Custom Start Command**:
      `/bin/sh -c 'vault server -dev -dev-listen-address=0.0.0.0:8200'`
  - The custom start command **overrides the image entrypoint** (the `vault` binary), so you must
        name the binary in full — `vault server …`, not just `server …`. Otherwise Railway tries to
        exec a binary literally named `server` and fails.
  - Listen on a **fixed 8200** so it matches the domain's target port (next bullet). (You *can* use
        `$PORT` instead, but only if Railway's `PORT` for this service equals the domain port — fixed
        8200 avoids that ambiguity.)
- [X] **Variables** (on the Vault service): add `VAULT_DEV_ROOT_TOKEN_ID` = `root`.
- [X] **Settings → Networking → Generate Domain** → set the **target port to `8200`** (must equal
      Vault's listen port above). Gives Vault a public HTTPS URL like `https://<vault>.up.railway.app`
      (HTTPS on 443 externally → forwards to 8200 inside). You'll point the backend's `VAULT_ADDR` at
      this in Step 5 — **with no `:8200`** (that port is internal).
  - ⚠️ **Security:** this exposes a dev-mode Vault (root token `root`) to the internet — acceptable
        only for a throwaway demo. Private-network alternative: start with
        `-dev-listen-address=[::]:8200` (IPv6, no public domain) and set
        `VAULT_ADDR=http://${{Vault.RAILWAY_PRIVATE_DOMAIN}}:8200`.
- [X] Seeding is automatic — the backend's start command runs `scripts/seed_vault.sh` on every boot.

> Production-grade Vault (HCP Vault or a sealed instance with real secrets) is the Phase 5
> follow-up; dev mode is only for getting a green public deploy now.

---

## 5. Set the backend service variables

Open the **backend service — this is the `sous-chef` service you deployed from GitHub in Step 1**
(the one that builds the Dockerfile / runs the FastAPI app), **not** Vault/Postgres/Redis. Go to its
**Variables** tab → add each. Use Railway variable references where shown so they stay in sync.

- [X] `ENV` = `production`
- [X] `VAULT_ADDR` = the Vault **public** URL from Step 4, e.g. `https://<vault>.up.railway.app`
      *(no port — Railway's edge serves HTTPS on 443 and forwards to Vault's `$PORT`)*. If you took
      the private-network route instead, use `http://${{Vault.RAILWAY_PRIVATE_DOMAIN}}:8200`.
- [X] `VAULT_TOKEN` = `root`  *(the dev root token from Step 4)*
- [X] `POSTGRES_URL` = the Railway Postgres URL **with the driver scheme rewritten** to
      `postgresql+psycopg://` (the app uses the psycopg3 driver; Railway gives plain `postgresql://`).
      Rebuild it from Railway's PG references (swap `Postgres` for your service's name):
        `postgresql+psycopg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.PGHOST}}:${{Postgres.PGPORT}}/${{Postgres.PGDATABASE}}`
  - `PGHOST` is the **private** internal address, so DB traffic stays inside Railway. The only literal
        change vs. the URL Railway gives you is the `+psycopg` in the scheme.
- [X] `REDIS_URL` = `${{Redis.REDIS_URL}}`  *(reference the Redis service)*
- [X] `PHOENIX_COLLECTOR_ENDPOINT` = `http://localhost:6006`
  - This field is **required** by config but tracing is best-effort; a placeholder is fine and the
        app still boots if Phoenix isn't running. (Optional: deploy a Phoenix service too and point
        this at its private URL.)

---

## 6. Trigger the deploy

- [X] Merge to `main` (or, while testing, use the service's **Deploy** button on your branch).
- [X] Watch **Deploy Logs**. In order you should see:
  1. `alembic upgrade head` running (creates the `vector` extension + tables)
  2. `seed_vault: wrote secret/sous-chef to http://...:8200`
  3. `vault.secrets_loaded` then uvicorn `Application startup complete`
- [X] If it crash-loops, read the first error in the logs:
  - `Could not load secrets from Vault` → Vault service down or `VAULT_ADDR`/`VAULT_TOKEN` wrong.
  - `extension "vector" is not available` → fix Step 2 (pgvector image).
  - `ValidationError` for a setting → a required variable in Step 5 is missing/misnamed.

---

## 7. Expose and verify the public URL

- [X] Backend service → **Settings → Networking → Generate Domain** → you get
      `https://<name>.up.railway.app`.
- [X] Railway's healthcheck (`/health`, from [railway.toml](railway.toml)) must turn **green** before
      the deploy is promoted. A `503` holds the rollout (that's the no-false-healthy guarantee).
- [X] From your machine:
      `curl -i https://<name>.up.railway.app/health`
      → expect **HTTP 200** + `{"status":"ok","dependencies":{"postgres":"ok","redis":"ok","vault":"ok"}}`.

---

## 8. Record it (closes the task)

- [X] Fill in the blanks in [docs/RUNBOOK.md](docs/RUNBOOK.md) → "Connect & verify the deploy":
      public URL, today's date, and tick the `200 ok` box.
- [X] Mark **T033** `[X]` in [specs/001-foundation/tasks.md](specs/001-foundation/tasks.md).
- [X] Delete this `todo.md` (or keep it as a deploy runbook — your call).

---

### Gotchas recap (the things that actually break it)

1. **`POSTGRES_URL` must use `postgresql+psycopg://`** — Railway's default `postgresql://` will fail.
2. **Postgres must have pgvector** — verify in Step 2 before deploying.
3. **Vault must be reachable + use the private domain** (`*.railway.internal`), not a public URL.
4. **`PHOENIX_COLLECTOR_ENDPOINT` is required** even though tracing is optional — set a placeholder.

#Fix nutrition when you start US2 — wire the Food.com nutrition column then. this is to fix data nutritonal value approximation problem