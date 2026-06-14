# Running the Dashboard + Phoenix WITHOUT upgrading the Railway plan

The workspace `Amer Aljoundy's Projects` is on the **Free/trial plan at its 5-service cap**
(`sous-chef` backend, `efficient-dream` Vault, `Redis`, `widget`, `Postgres`). Railway therefore
refuses a 6th/7th service — `railway add` returns *"Free plan resource provision limit exceeded."*
So the operator **dashboard** (T017b) and **Phoenix** (T017c) cannot be *new Railway services* without
either upgrading the plan or freeing a slot.

This guide gets both working **without paying**, by running them **locally against the live prod stack**.
Both are operator-only/unadvertised surfaces, so hosting them on your own machine instead of Railway is
functionally equivalent for an operator.

> Three strategies, pick per service:
> - **A. Dashboard → run locally against prod.** Fully works, permanent-enough, zero Railway slots. ✅
> - **B. Phoenix → run locally (Docker) against the prod Postgres.** UI works immediately; to also capture
>   spans *from the prod backend* you expose it with a quick tunnel and repoint one backend var.
> - **C. (optional) Free a real Railway slot** by moving the static `widget` to a free static host, then
>   deploy ONE of these on Railway proper. See the end.

---

## 0. One-time prerequisites

```powershell
# --- paste these two secrets into THIS shell session (they are NOT stored in any file) ---
# Prod Vault root token (the one you use to unseal/seed prod Vault; it's in your local .env):
$VAULT_ROOT_TOKEN = "<paste prod Vault root token, e.g. hvs....>"
# Prod Postgres PUBLIC connection string — copy DATABASE_PUBLIC_URL from:
#   Railway → zonal-perception → Postgres service → Variables → DATABASE_PUBLIC_URL
# (looks like: postgresql://postgres:<pw>@maglev.proxy.rlwy.net:23079/railway)
$PROD_PG_URL = "<paste DATABASE_PUBLIC_URL>"

# --- fixed prod locators (safe to keep literal; no secrets) ---
$PROD_BACKEND  = "https://sous-chef-production-721e.up.railway.app"
$PROD_VAULT    = "https://efficient-dream-production-e88d.up.railway.app"   # operator-only Vault ingress
```

**Vault must be UNSEALED** (it re-seals on every redeploy — deviation T017e). The dashboard reads its
secrets from Vault at startup, so unseal first:

```powershell
$env:VAULT_ADDR = $PROD_VAULT
vault status                     # if "Sealed: true":
vault operator unseal            # paste your unseal key, repeat until Sealed: false
```

> If you don't have the `vault` CLI, unseal via the API:
> ```powershell
> curl.exe -s --request PUT --data '{\"key\":\"<UNSEAL_KEY>\"}' "$PROD_VAULT/v1/sys/unseal"
> ```

---

## A. Operator dashboard — run locally against prod

The dashboard is a Streamlit app that talks to the prod backend `/admin/*` (admin token from Vault) and
reads its login secrets from prod Vault. Running it on your machine pointed at prod is the no-slot path.

### A.1 Confirm the dashboard secrets exist in prod Vault

The login needs `OPERATOR_PASSWORD_HASH`, `DASHBOARD_COOKIE_KEY`, and `ADMIN_API_TOKEN` at KV v2
`secret/sous-chef`. Check:

```powershell
$env:VAULT_ADDR = $PROD_VAULT
curl.exe -s -H "X-Vault-Token: $VAULT_ROOT_TOKEN" "$PROD_VAULT/v1/secret/data/sous-chef" `
  | python -c "import sys,json; d=json.load(sys.stdin)['data']['data']; print('present keys:', sorted(d))"
```

If `OPERATOR_PASSWORD_HASH` / `DASHBOARD_COOKIE_KEY` / `ADMIN_API_TOKEN` are missing, seed them once
(real values from your shell), e.g. via the repo's seed script against prod Vault:

```powershell
# bash (Git Bash / WSL): export the prod addr+token, then run the idempotent seeder
VAULT_ADDR="$PROD_VAULT" VAULT_TOKEN="$VAULT_ROOT_TOKEN" sh scripts/seed_vault.sh
```

> The same `ADMIN_API_TOKEN` value must be what the backend expects — since both read the *same* prod
> Vault, they stay consistent automatically.

### A.2 Run the dashboard

```powershell
$env:VAULT_ADDR        = $PROD_VAULT
$env:VAULT_TOKEN       = $VAULT_ROOT_TOKEN
$env:BACKEND_ADMIN_URL = $PROD_BACKEND
$env:OPERATOR_USERNAME = "operator"

uv sync --extra dashboard
uv run streamlit run dashboard/app.py --server.port=8501
```

### A.3 Verify

- Open **http://localhost:8501**.
- Log in: user `operator`; password is whatever the seeded `OPERATOR_PASSWORD_HASH` encodes (the repo's
  dev placeholder is `souschef-dev` — change it for a real deployment).
- Confirm the **Corpus / Evals / Metrics** pages load and a page refresh keeps you logged in.
- The dashboard is now driving prod read-only/admin endpoints from your machine. Close the terminal to
  stop it; nothing was provisioned on Railway.

---

## B. Phoenix tracing — run locally (Docker) against prod Postgres

Phoenix persists traces in the **shared prod Postgres**, in the `phoenix` schema (already created in prod).
Running the upstream image locally gives you the Phoenix UI immediately.

### B.1 Start Phoenix locally

`PHOENIX_SQL_DATABASE_URL` must be a plain `postgresql://` URL (not `+psycopg`). `DATABASE_PUBLIC_URL`
already has that form.

```powershell
docker run -d --name phoenix-prod -p 6006:6006 `
  -e PHOENIX_SQL_DATABASE_URL="$PROD_PG_URL" `
  -e PHOENIX_SQL_DATABASE_SCHEMA=phoenix `
  arizephoenix/phoenix:latest

docker logs -f phoenix-prod          # wait until it runs migrations + serves on 6006 (Ctrl-C to stop tailing)
```

Open **http://localhost:6006**. On first boot Phoenix creates its tables in the `phoenix` schema. It will
be empty until something sends it spans (next step).

### B.2 (Optional) Capture spans FROM the prod backend

The prod backend ships spans to `PHOENIX_COLLECTOR_ENDPOINT` over OTLP/HTTP (it appends `/v1/traces`, no
auth). For prod spans to reach your local Phoenix, the prod backend needs a public URL that forwards to
`localhost:6006`. Use a quick tunnel:

```powershell
# In a SEPARATE terminal — needs cloudflared (winget install Cloudflare.cloudflared) or ngrok:
cloudflared tunnel --url http://localhost:6006
# → copy the printed https URL, e.g. https://random-words.trycloudflare.com
```

Point the backend at it (this redeploys the backend; tracing is best-effort so `/health` is unaffected):

```powershell
$env:RAILWAY_TOKEN = "<a project token, or run: railway login; railway link>"
railway variables --service sous-chef --set "PHOENIX_COLLECTOR_ENDPOINT=https://random-words.trycloudflare.com"
```

Now generate prod traffic and watch traces appear in your local Phoenix:

```powershell
curl.exe -s "$PROD_BACKEND/health" > $null
# (or run the cook demo against the prod widget)
```

> ⚠️ This is **ephemeral**: it only works while your local Phoenix **and** the tunnel are running, and the
> tunnel URL changes each run. When you're done, revert the backend so it isn't pointing at a dead tunnel:
> ```powershell
> railway variables --service sous-chef --set "PHOENIX_COLLECTOR_ENDPOINT=http://localhost:6006"
> ```
> (Leaving it at a dead endpoint is harmless — tracing just no-ops — but reverting keeps things tidy.)

### B.3 Stop Phoenix

```powershell
docker rm -f phoenix-prod
```

---

## C. Free a real Railway slot — RECOMMENDED for the dashboard

You can free a slot **without paying and without giving anything up that matters**, then deploy the
dashboard as a proper Railway service.

### C.1 (best) Remove Redis — it's optional

Redis's only job here is the dashboard's best-effort routing-split counter; it's used for nothing on the
cook path, and `REDIS_URL` is now optional in code (the app runs cache-less and `/health` drops the redis
check). So deleting the `Redis` service frees a slot with **zero impact on the cook journey** — you only
lose the routing-split metric (it reads `0% / 0%`). Full rationale + the exact steps:
[`docs/RUNBOOK.md`](docs/RUNBOOK.md) → *"Redis is optional — remove it to free a Railway service slot."*

```powershell
# after this code is on main and the backend redeployed:
$env:RAILWAY_TOKEN = "<project token, or: railway login; railway link>"
railway variables --service sous-chef --remove REDIS_URL    # backend redeploys; /health -> {postgres,vault} = 200
# then delete the Redis service in the Railway UI (Service -> Settings -> Delete)
```

Now create the **dashboard** in that freed slot — see §D below (or the per-service steps in
`docs/RUNBOOK.md`).

### C.2 (alternative) Move the static widget off Railway

If you also need a slot for Phoenix, free a second one by moving the static `widget` to a free static host
(it's just a Vite build — any free host serves it):

1. **Build the widget** pointed at the prod backend:
   ```powershell
   cd widget
   npm install
   $env:VITE_API_BASE = $PROD_BACKEND
   npm run build            # outputs ./dist
   cd ..
   ```
2. **Host `widget/dist/`** on a free static host — Cloudflare Pages, Netlify drop, or GitHub Pages.
   Note the new public origin, e.g. `https://souschef.pages.dev`.
3. **Allow it in CORS** on the backend:
   ```powershell
   railway variables --service sous-chef --set "WIDGET_ORIGINS=https://souschef.pages.dev"
   ```
4. **Delete the Railway `widget` service** (frees one slot): Railway → `widget` → Settings → Delete Service.
5. You now have a free Railway slot — deploy **dashboard** *or* **phoenix** as a real service following the
   per-service steps in `docs/RUNBOOK.md` → *"Known deployment deviations"* and the `railway/*.toml` files.
   (Repeat the widget move for a second slot if you want both on Railway.)

---

## D. Create the dashboard as a Railway service (after freeing a slot via §C.1)

Once the Redis slot is free, deploy the dashboard from the repo. It builds from `dashboard/Dockerfile`,
so you **must** point it at `railway/dashboard.toml` or it inherits the root `railway.toml` and wrongly
runs the backend's start command (the T017g trap).

```powershell
$env:RAILWAY_TOKEN = "<project token, or: railway login; railway link>"

# 1) create from the repo + set its bootstrap (non-secret) variables
railway add --service dashboard --repo klc33/sous-chef --branch main --variables 'VAULT_ADDR=http://efficient-dream.railway.internal:8200' --variables 'VAULT_TOKEN=${{sous-chef.VAULT_TOKEN}}' --variables 'BACKEND_ADMIN_URL=https://sous-chef-production-721e.up.railway.app' --variables 'OPERATOR_USERNAME=operator'
```

2. **Set the config-as-code path** (CLI can't; do it in the UI or API):
   - UI: `dashboard` → Settings → Config-as-code → `railway/dashboard.toml`.
   - API (PowerShell):
     ```powershell
     $T = "<workspace token>"; $ENVID = "d11a2bec-c0d3-4e2f-ad5f-977dfef26d6e"; $SVC = "<dashboard service id from: railway status>"
     $body = @{ query = "mutation { serviceInstanceUpdate(environmentId: \`"$ENVID\`", serviceId: \`"$SVC\`", input: { railwayConfigFile: \`"railway/dashboard.toml\`" }) }" } | ConvertTo-Json
     Invoke-RestMethod -Method Post -Uri "https://backboard.railway.app/graphql/v2" -Headers @{ Authorization = "Bearer $T" } -ContentType "application/json" -Body $body
     ```
3. **Generate an (unadvertised) domain**: `dashboard` → Settings → Networking → Generate Domain.
4. **Prereqs** (same as §A): prod Vault **unsealed**, and the dashboard secrets
   (`OPERATOR_PASSWORD_HASH`, `DASHBOARD_COOKIE_KEY`, `ADMIN_API_TOKEN`) present at Vault `secret/sous-chef`.
5. **Redeploy + verify**: `railway redeploy --service dashboard`, then open the generated domain and log in
   (`operator` / your seeded password).

---

## What "done" looks like (no upgrade)

- **Dashboard**: either a real Railway service in the slot freed by removing Redis (§C.1 + §D), or run
  locally at `http://localhost:8501` against prod (§A).
- **Phoenix**: run locally at `http://localhost:6006` (§B), optionally receiving prod spans via the tunnel,
  or give it a real slot by also moving the widget off Railway (§C.2). A standing prod-hosted Phoenix needs
  that second slot (or a plan upgrade).
- **Cook journey is unaffected** throughout — removing Redis only drops the dashboard's routing-split
  metric.

> Security notes: this file contains **no secrets** — `$VAULT_ROOT_TOKEN` and `$PROD_PG_URL` are pasted
> into your shell session only. The prod Vault public endpoint is operator-only and root-token-gated; the
> Postgres public proxy is password-gated. Don't commit your `.env` (it's gitignored).
