# TODO — Deploy SousChef backend to Railway

Step-by-step to take the foundation backend live on a public Railway URL gated on `/health`
(closes **FR-008 / SC-005**, task **T033**). Work top to bottom; tick each box as you go.

> **Already done for you (code):** [railway.toml](railway.toml) `startCommand` now runs
> `alembic upgrade head` → `seed_vault.sh` → `uvicorn`, so the deploy migrates + seeds itself.
> Everything below is dashboard work that needs your Railway account.

---

## 0. Prerequisites

- [ ] Railway account: https://railway.app (sign in with GitHub).
- [ ] Repo pushed to GitHub. Railway auto-deploys the **`main`** branch, so your foundation work
      must be merged to `main` before the first real deploy. (You can deploy a branch manually
      while testing.)
- [ ] CI is green on the branch you'll deploy (ruff + mypy + smoke).

---

## 1. Create the project and link GitHub

- [ ] Railway → **New Project** → **Deploy from GitHub repo** → select your `sous-chef` repo.
- [ ] Open the created **backend service** → **Settings → Build**: confirm builder is **Dockerfile**
      (auto-detected from [railway.toml](railway.toml); no manual config needed).
- [ ] **Settings → Deploy**: confirm **Auto Deploy** is ON and the branch is **`main`**.

---

## 2. Add PostgreSQL (must have pgvector)

- [ ] Project canvas → **New → Database → Add PostgreSQL**.
- [ ] Open the Postgres service → **Data / Query** tab → run:
      `CREATE EXTENSION IF NOT EXISTS vector;`
  - ✅ Succeeds → good.
  - ❌ Errors (`extension "vector" is not available`) → the default image lacks pgvector. Delete it
        and instead add Railway's **pgvector template** (search templates for "pgvector"), or the
        baseline migration will fail at deploy.
- [ ] Note: you do **not** need to create tables — the deploy's `alembic upgrade head` does that.

---

## 3. Add Redis

- [ ] Project canvas → **New → Database → Add Redis**.
- [ ] No further config; you'll reference its URL in Step 5.

---

## 4. Add a Vault service (dev mode, for the demo)

The app loads secrets from Vault at startup and **fails fast if Vault is unreachable**. Simplest
working option for a demo:

- [ ] Project canvas → **New → Empty Service**.
- [ ] **Settings → Source → Image**: `hashicorp/vault:latest`.
- [ ] **Settings → Deploy → Custom Start Command**:
      `server -dev -dev-listen-address=0.0.0.0:8200`
- [ ] **Variables** (on the Vault service): add `VAULT_DEV_ROOT_TOKEN_ID` = `root`.
- [ ] **Settings → Networking**: note its **private** address, e.g. `vault.railway.internal` (port `8200`).
- [ ] Seeding is automatic — the backend's start command runs `scripts/seed_vault.sh` on every boot.

> Production-grade Vault (HCP Vault or a sealed instance with real secrets) is the Phase 5
> follow-up; dev mode is only for getting a green public deploy now.

---

## 5. Set the backend service variables

Open the **backend** service → **Variables** → add each. Use Railway variable references where
shown so they stay in sync.

- [ ] `ENV` = `production`
- [ ] `VAULT_ADDR` = `http://vault.railway.internal:8200`  *(your Vault private URL from Step 4)*
- [ ] `VAULT_TOKEN` = `root`  *(the dev root token from Step 4)*
- [ ] `POSTGRES_URL` = the Railway Postgres URL **with the driver prefix rewritten** to
      `postgresql+psycopg://…`
  - Railway gives `postgresql://user:pass@host:port/db`. You must change the scheme to
        `postgresql+psycopg://` (same rest of the string). Example using a reference:
        `postgresql+psycopg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.RAILWAY_PRIVATE_DOMAIN}}:5432/${{Postgres.PGDATABASE}}`
- [ ] `REDIS_URL` = `${{Redis.REDIS_URL}}`  *(reference the Redis service)*
- [ ] `PHOENIX_COLLECTOR_ENDPOINT` = `http://localhost:6006`
  - This field is **required** by config but tracing is best-effort; a placeholder is fine and the
        app still boots if Phoenix isn't running. (Optional: deploy a Phoenix service too and point
        this at its private URL.)

---

## 6. Trigger the deploy

- [ ] Merge to `main` (or, while testing, use the service's **Deploy** button on your branch).
- [ ] Watch **Deploy Logs**. In order you should see:
  1. `alembic upgrade head` running (creates the `vector` extension + tables)
  2. `seed_vault: wrote secret/sous-chef to http://...:8200`
  3. `vault.secrets_loaded` then uvicorn `Application startup complete`
- [ ] If it crash-loops, read the first error in the logs:
  - `Could not load secrets from Vault` → Vault service down or `VAULT_ADDR`/`VAULT_TOKEN` wrong.
  - `extension "vector" is not available` → fix Step 2 (pgvector image).
  - `ValidationError` for a setting → a required variable in Step 5 is missing/misnamed.

---

## 7. Expose and verify the public URL

- [ ] Backend service → **Settings → Networking → Generate Domain** → you get
      `https://<name>.up.railway.app`.
- [ ] Railway's healthcheck (`/health`, from [railway.toml](railway.toml)) must turn **green** before
      the deploy is promoted. A `503` holds the rollout (that's the no-false-healthy guarantee).
- [ ] From your machine:
      `curl -i https://<name>.up.railway.app/health`
      → expect **HTTP 200** + `{"status":"ok","dependencies":{"postgres":"ok","redis":"ok","vault":"ok"}}`.

---

## 8. Record it (closes the task)

- [ ] Fill in the blanks in [docs/RUNBOOK.md](docs/RUNBOOK.md) → "Connect & verify the deploy":
      public URL, today's date, and tick the `200 ok` box.
- [ ] Mark **T033** `[X]` in [specs/001-foundation/tasks.md](specs/001-foundation/tasks.md).
- [ ] Delete this `todo.md` (or keep it as a deploy runbook — your call).

---

### Gotchas recap (the things that actually break it)

1. **`POSTGRES_URL` must use `postgresql+psycopg://`** — Railway's default `postgresql://` will fail.
2. **Postgres must have pgvector** — verify in Step 2 before deploying.
3. **Vault must be reachable + use the private domain** (`*.railway.internal`), not a public URL.
4. **`PHOENIX_COLLECTOR_ENDPOINT` is required** even though tracing is optional — set a placeholder.
