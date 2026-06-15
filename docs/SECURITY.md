# Security — 003 Intelligent Behavior

How the intelligent layer stays safe. Two guarantees dominate: **the allergen wall holds on every new
recipe path**, and **manipulation is refused deterministically**. Both are enforced in code (not prompts)
and covered by committed gates. Constitution principles cited as `P#`.

## Threat model

The untrusted input is the cook's free-text chat message (and, indirectly, any text a tool surfaces).
The assets we protect: (1) a cook's declared allergies/diet — surfacing a violating recipe is the worst
outcome; (2) provider secrets (Groq / **OpenAI** / embeddings / Vault keys); (3) the assistant's own
instructions (no system-prompt leak, no role takeover).

## 1. The allergen wall — deterministic, on every path

The wall is the grade (golden rule #1). Every recipe that can reach a cook is rendered **only** through
[app/services/shared/recipe_view.py](../app/services/shared/recipe_view.py), which requires a
`ConstraintProfile` and calls the deterministic
[app/services/user/constraint_guard.py](../app/services/user/constraint_guard.py). New paths added this
phase, all funneled through that one choke point:

| Path | Where the wall runs |
|---|---|
| RAG search | `rag.retrieve` filters the over-fetched pool via `constraint_guard.filter` (fail-closed) before top-3 |
| Agent tools | each recipe-returning tool wall-clears output via `recipe_view` before returning to the loop |
| Meal plan | every planned day's recipe is wall-cleared; `cuisine IS NULL` never counts toward variety |
| Substitution | curated map entries filtered to drop any substitute that contains/may-contain a declared allergen (fail-closed) |
| Output rail | re-asserts the wall by re-fetching each surfaced recipe by id and re-checking — defense in depth |

**Fail-closed everywhere**: a recipe whose allergens are uncertain, or whose id can't be parsed/resolved
on the output path, is treated as a violation and dropped — uncertainty never favors surfacing.

**Enforced by**: [tests/integration/test_wall_regression.py](../tests/integration/test_wall_regression.py)
enumerates every recipe path (recipe detail, RAG, agent tools, meal plan) and asserts a violating recipe
can never be surfaced. Adding an intelligent path that forgets the wall fails CI (SC-006).

## 2. Input rail — refuse manipulation before routing (P6)

[app/guardrails/input_rails.py](../app/guardrails/input_rails.py) screens the message with deterministic
regex/keyword patterns (no model, so the gate is reproducible) **before** it reaches the router:

- **Allergen/diet-override** ("ignore my peanut allergy", "override my diet", "I don't really have a dairy
  allergy") → **REFUSE the whole turn** with a safe message. The "safe remainder" of such a request is
  inseparable from an unsafe ask, so none of it is served. (The wall makes it structurally impossible past
  retrieval too — this is belt-and-suspenders.)
- **Injection / jailbreak / role-override / prompt-leak** ("ignore previous instructions", "you are now…",
  "reveal your system prompt", "DAN/developer mode") → **strip the offending sentence**. If a meaningful
  safe remainder survives (an injection embedded in a valid cooking request, FR-033), the **cleaned**
  remainder is served; if nothing safe is left, REFUSE.
- Otherwise allow-by-default: an unmatched message passes through unchanged.

A refusal never echoes the probe and never reveals internals. The `/chat` endpoint short-circuits on
`refuse` → `ChatResponse(refused=true, ...)` before any routing/LLM call.

**Enforced by**: [tests/redteam/test_attempts.py](../tests/redteam/test_attempts.py) drives
[evals/redteam/attempts.yaml](../evals/redteam/attempts.yaml) (17 probes across allergen-override,
injection, jailbreak, prompt-leak) at a hard **refusal_rate = 1.0** — a single un-refused probe fails the
build. The embedded-injection-in-a-valid-request case (neutralize + serve remainder) is covered in
[tests/unit/test_guardrails.py](../tests/unit/test_guardrails.py).

## 3. Output rail — redact + re-assert before anything leaves (P5, P6)

[app/guardrails/output_rails.py](../app/guardrails/output_rails.py) is the last gate every turn passes:

1. **Redaction** — runs [app/core/redaction.py](../app/core/redaction.py) over the free-text `reply` (the
   only field that can carry a leaked secret/PII value) so nothing sensitive reaches logs **or** a tracing
   span. This runs before the reply leaves **and** before any span is emitted (golden rule #5). The span
   destination is pluggable (`TRACING_PROVIDER`: self-hosted **Phoenix** by default, or **LangSmith Cloud**
   in prod — see [DECISIONS.md](DECISIONS.md) D11), but redaction runs in the **same `_RedactingSpanExporter`
   wrapper** for both, so no secret/PII egresses even when spans go to the third-party cloud sink. The
   LangSmith API key itself is a Vault secret, never in env/image (golden rule #4).
2. **Wall re-assertion** — re-fetches each surfaced recipe by id and re-runs `constraint_guard`, dropping
   any violator (fail-closed on an unparseable/unresolvable id).

**Enforced by**: [tests/unit/test_redaction.py](../tests/unit/test_redaction.py) and the
`redaction.leak_count_max: 0` gate (0 leaks tolerated over a battery of provider/Groq/bearer/Vault-shaped
secrets).

## 4. Injection-safe data access & secret handling

- **DB**: all recipe access goes through [app/repo/recipes.py](../app/repo/recipes.py) with ORM /
  parameterized queries only; the vector search binds `query_vec` and `exclude_ids` as parameters
  (injection-safe, P3, P6).
- **Secrets**: Groq + **OpenAI** + embeddings keys are read from Vault at runtime; `.env.example` holds
  only the Vault addr/token + service URLs — **no keys in code, `.env`, or any image** (P4). No torch in
  any image (P3). `OPENAI_API_KEY` (the chat key used only when `LLM_PROVIDER=openai`) is resolved through
  the **exact same** `VaultAdapter` path as `GROQ_API_KEY` — seeded by [scripts/seed_vault.sh](../scripts/seed_vault.sh)
  (env-forward-or-dev-placeholder), read at startup, dormant while the default `groq` provider is active. See
  [DECISIONS.md](DECISIONS.md) **D9** for the provider-agnostic seam.
- **Agent bounds**: the loop is capped in iterations + tokens and every tool input is Pydantic-validated
  (P6, SC-007), so a manipulated turn can't drive unbounded tool use.
- **Identity**: the cook is an **admin-provisioned account** authenticated by a cook-session JWT (009,
  §6); the owner/tenant is the verified token's `sub` and is never taken from a request body or header.

## 5. Operator auth — admin-managed accounts + per-user JWT (008, supersedes 004's shared token)

The operator surface (the `/admin/*` API + the Streamlit dashboard) adds **no end-user auth system**; it is
**admin-managed, named accounts** authenticated by a signed JWT (008-admin-managed-operator-accounts;
constitution 2.0.0 permits operator-console auth). This **replaces** 004's single shared `ADMIN_API_TOKEN` +
single `OPERATOR_PASSWORD_HASH` — both **retired**. The login token is signed with a Vault key, but **password
hashes are NOT a Vault secret**: a bcrypt hash is one-way (non-reversible), so it lives in the
`operator_accounts.password_hash` DB column, not Vault. The two Vault keys this surface needs
(`JWT_SIGNING_KEY`, `BOOTSTRAP_ADMIN_PASSWORD`) are seeded by [scripts/seed_vault.sh](../scripts/seed_vault.sh)
and read at startup via [app/infra/vault.py](../app/infra/vault.py); the backend **fails fast** if either is
missing.

| Boundary | Mechanism |
|---|---|
| **Human → backend (login)** | `POST /admin/auth/login` ([app/api/admin/auth.py](../app/api/admin/auth.py)) verifies username/password against `operator_accounts` (bcrypt) in [app/core/security.py](../app/core/security.py) and, on success, mints a short-lived **HS256 JWT** carrying `{sub, role, iat, exp}` signed with the Vault `JWT_SIGNING_KEY` (TTL = `JWT_TTL_MINUTES`, default 8h — the authoritative session clock). Auth is **constant-time** with a dummy-verify on unknown users and a **single generic 401** for both wrong-password and unknown-username (no account enumeration, FR-012). |
| **Human → dashboard (session)** | The dashboard ([dashboard/auth.py](../dashboard/auth.py)) stores the backend-issued JWT in a cookie signed with the Vault `DASHBOARD_COOKIE_KEY`, so a **refresh stays signed in** (FR-013). The cookie window is set ≥ the JWT TTL so the cookie never lapses before a still-valid token; an expired/absent JWT is treated as logged-out regardless of the cookie. The dashboard **never sees a password hash and never mints a token** — it only stores the JWT it is handed. |
| **Dashboard → backend (authz)** | The dashboard attaches `Authorization: Bearer <jwt>` on every `/admin/*` call. [app/api/admin_deps.py](../app/api/admin_deps.py) splits authorization into **`require_operator`** (verify signature + expiry against `JWT_SIGNING_KEY`, then re-load the `sub` account and require it still exist and be **active**) and **`require_admin`** (additionally `role == 'admin'`). The role is enforced **server-side in code**, never merely hidden in the UI (FR-011). |

**Boundary properties**:

- **Role split is server-side.** Every `/admin/users/*` route is gated by `require_admin`; a valid `user`-role
  token is authenticated but gets **403 `admin_forbidden`** on those routes. Other operational `/admin/*` routes
  and `/admin/auth/me` take `require_operator`; `/admin/auth/login` is the only unauthenticated `/admin` route.
- **Immediate revocation.** Because `require_operator` re-loads the `sub` account and re-checks `is_active` on
  **every** request, a deactivated (or deleted) account is denied on its very next call even with an unexpired
  token (research D2) — deactivation does not wait for the JWT to expire.
- **No self-service & always recoverable.** There is **no sign-up** anywhere; only an admin provisions accounts.
  A first admin is **bootstrapped** at startup from `BOOTSTRAP_ADMIN_USERNAME` (config) + `BOOTSTRAP_ADMIN_PASSWORD`
  (Vault) when the accounts table is empty — idempotent, so a fresh deploy always has a way in and a restart
  never duplicates it. The **last active admin can never be deactivated** (409 `last_admin`), so the console can
  never be locked out.
- The **public widget holds no token** and cannot reach `/admin/*` (FR-029) — it only calls the
  profile-scoped user endpoints. The admin and cook surfaces share the monolith but not the trust level.
- The dashboard reads Vault **over HTTP** (it carries `httpx`, not `hvac`) and **never imports the `app`
  package** — it is the dashboard image's only secrets touchpoint, keeping that image lean and decoupled.
- **Per-user accountability with no secret leak.** The account service emits an audit log line for each
  create/deactivate/reset recording the action + actor, and redaction guarantees **no password, hash, or token**
  value reaches a log or a Phoenix span (golden rule #5).

## 6. Cook auth — admin-provisioned cook accounts + total gating (009, replaces the passwordless profile-ID)

The cook-facing app is now **gated behind login**: the passwordless `X-Profile-ID` header is **retired**
(009-admin-managed-cook-accounts; constitution 2.0.0 permits admin-provisioned end-user auth). A cook is an
**admin-provisioned, named account** ([app/models/cook_account.py](../app/models/cook_account.py)) and signs
in on the React widget (a professional login screen — **no self-signup**). The cook's diet/allergy profile,
favorites, and seen-history are **account-owned** (the owner key is the cook account id), so each cook sees
only their own data. Like operator accounts, a bcrypt password hash is **not** a Vault secret — it lives in
the `cook_accounts.password_hash` DB column.

| Boundary | Mechanism |
|---|---|
| **Cook → backend (login)** | `POST /auth/login` ([app/api/user/auth.py](../app/api/user/auth.py)) verifies username/password against `cook_accounts` (bcrypt, via [app/core/security.py](../app/core/security.py)) and mints a short-lived **HS256 JWT** carrying `{sub, typ:"cook", iat, exp}` signed with the Vault `COOK_SESSION_KEY` (TTL = `COOK_JWT_TTL_MINUTES`, default 8h). Auth is **constant-time** with a dummy-verify on unknown users and a **single generic 401** (no account enumeration). `/auth/login` + `/health` are the only open cook routes. |
| **Cook → backend (authz)** | The widget attaches `Authorization: Bearer <jwt>` on every request. [app/api/deps.py](../app/api/deps.py) `require_cook` verifies signature + expiry **and the `typ:"cook"` claim** against `COOK_SESSION_KEY`, then re-loads the `sub` cook and requires it to still exist and be **active**. Every cook endpoint (`/chat`, `/recipes*`, `/favorites*`, `/profile`) is gated; an anonymous or legacy-`X-Profile-ID` request carries no Bearer token → **401 `cook_unauthorized`**. |

**Boundary properties**:

- **Separate identity domain (FR-013).** Cook sessions use their **own** Vault signing key (`COOK_SESSION_KEY`,
  distinct from the operator `JWT_SIGNING_KEY`) **and** a `typ:"cook"` claim that `decode_cook_token` requires.
  An operator (008) token therefore can never be replayed as a cook session, or vice-versa — the two trust
  domains never share a credential store or a key.
- **Total gating, no anonymous mode.** There is no bypass: even CI/evals and the live demo authenticate as a
  **seeded demo cook** (bootstrapped idempotently at startup from `DEMO_COOK_USERNAME` + the Vault
  `DEMO_COOK_PASSWORD`), so the wall/grounding/redaction/red-team gates all run on the **authenticated** path
  (SC-008). Recipes still leave only via `recipe_view`.
- **Immediate revocation.** Because `require_cook` re-loads the `sub` cook and re-checks `is_active` on every
  request, a deactivated cook is denied on its very next call even with an unexpired token (FR-009).
- **No self-service.** Only an operator **admin** (008 `require_admin`) creates/lists/deactivates/reactivates/
  resets cook accounts via `/admin/cooks` + the dashboard; a non-admin operator token gets 403. The widget has
  no signup control. Per-account accountability is preserved: create/deactivate/reset emit an audit log line
  (action + actor) and redaction guarantees no password/hash/token reaches a log or Phoenix span (golden rule #5).
- **Rate limiting** on `/chat` is re-keyed to the **cook account id** from the token (was the `X-Profile-ID`
  header), keeping the per-cook budget.

## 7. Operability surface — the LLM seam & pgAdmin (005)

Two operator-facing additions that, by construction, **cannot** weaken the two guarantees above.

**The provider swap never touches safety.** `LLM_PROVIDER` selects only the chat/agent *generation*
adapter inside [app/infra/llm/](../app/infra/llm/). The deterministic wall
([constraint_guard](../app/services/user/constraint_guard.py)), the input/output guardrails, and grounding
are unchanged code on every path regardless of provider — the swap adds no new generation path and removes
no gate. The facade attaches only **non-secret** span attributes (`llm.provider`, `llm.model`,
`llm.total_tokens`); redaction still runs over span attributes before export (golden rule #5), so no key
reaches a log or a Phoenix span under either provider. The [contract test](../tests/contract/test_llm_client.py)
proves both adapters expose the identical tool-call shape **with no network**, and the wall-regression +
red-team suites stay green under whichever provider is active (SC-005) — safety is provider-independent and
proven, not assumed.

**pgAdmin is a local-only convenience, never deployed (P10, FR-015/FR-016).** The `pgadmin` service lives
under the docker-compose **`local` profile** (activated by `make up`); a bare `docker compose up` omits it,
and Railway deploys only the backend (`railway.toml`), so it is excluded from production **doubly** —
structurally and by the profile. Its `PGADMIN_DEFAULT_EMAIL`/`PGADMIN_DEFAULT_PASSWORD` are obvious
local-only placeholders in `.env.example` — **not** Vault secrets, never logged/traced/deployed. The
Postgres password pgAdmin connects with is the existing dev default; `servers.json` ships only connection
*metadata* (host/port/db/user), no password.

**A manual pgAdmin edit cannot bypass the wall (FR-018).** pgAdmin is read-write by design (the spec wants
inspect **and** repair), but the constraint guard reads `recipes.allergens` **fresh at query time on every
cook-facing output path** — so an operator's `UPDATE` to that column is filtered on the very next request by
construction. Verified by the wall-regression suite (which enumerates `/recipes`, `/recipes/{id}`, `/chat`):
a manual data change can change *which* recipes exist, never whether an unsafe one can surface.

## 8. Deployment security — the secrets split & the limited public surface (007)

Shipping to a public URL adds two security properties, both enforced by topology rather than trust.

### 8a. The three-way secrets split (FR-004/005/006, SC-004)

Every value the running system needs falls into **exactly one** of three homes — **nothing secret is ever a
Railway variable, and no secret is ever in the repo, an image, or `.env`** (golden rule #4). Full keyspace
in [contracts/secrets-keyspace.md](../specs/007-ship-public-deploy/contracts/secrets-keyspace.md) and
[data-model.md](../specs/007-ship-public-deploy/data-model.md) §2.

| Home | What lives here | Examples | Rule |
|---|---|---|---|
| **Vault** (`secret/sous-chef`, KV v2) | **all application secrets** — the only home | `GROQ_API_KEY`, `EMBEDDINGS_API_KEY`, `OPENAI_API_KEY`, `LANGSMITH_API_KEY`, `JWT_SIGNING_KEY`, `BOOTSTRAP_ADMIN_PASSWORD`, `DASHBOARD_COOKIE_KEY`, `app_secret` | seeded once by [scripts/seed_vault.sh](../scripts/seed_vault.sh) into the persistent prod Vault; read at startup. (Per-user bcrypt password hashes are **not** Vault secrets — they are one-way and live in `operator_accounts.password_hash`, §5.) |
| **Platform-injected** | managed **datastore credentials** | `POSTGRES_URL` (Postgres plugin), `REDIS_URL` (Redis plugin, optional) | provided by Railway's managed plugins — never hand-set, never in Vault |
| **Railway variables** | **bootstrap + non-secret only** | `ENV`, `VAULT_ADDR`, `VAULT_TOKEN`, `TRACING_PROVIDER`, `LANGSMITH_PROJECT` (name), `WIDGET_ORIGINS`, `BACKEND_ADMIN_URL`, `BOOTSTRAP_ADMIN_USERNAME`, `JWT_TTL_MINUTES`, `VITE_API_BASE` | non-secret config selectors |

The **one** deliberate nuance: `VAULT_TOKEN` is a real `hvs.`-shaped token living as a Railway variable.
This is **contract-allowed by design** — it is the chicken-and-egg bootstrap credential the backend needs
*to reach* Vault, so it cannot itself live in Vault. It is bootstrap, not an application secret.

**Proven, not asserted (SC-004):** a repo + image key-shape scan (`gsk_…` / `sk-…` / `hvs.…` / hardcoded
`*_API_KEY=<literal>`, excluding test fixtures + prose) returns **zero real secrets**; the only hits are the
deliberately-fake redaction fixtures. **Fail-fast** is locked by [tests/unit/test_vault.py](../tests/unit/test_vault.py):
remove any required secret (or hit an unseeded Vault path) and `VaultAdapter.load_secrets()` raises
`StartupConfigError` with a seed-pointing message — the backend never boots silently degraded (FR-004/FR-014).

### 8b. The limited public surface (FR-001/FR-001a)

Only **two** services are reachable on the advertised public URL: the cook **`widget`** (static SPA) and the
**`backend` API** it calls. Everything else is private or unadvertised:

- **`dashboard`** (Streamlit operator console) and **tracing** (self-hosted Phoenix, or LangSmith Cloud)
  live on **separate, unadvertised URLs**, operator-gated (the dashboard behind `streamlit-authenticator`
  cookie login keyed from Vault, §5). They are deployed but never linked from the public app.
- **Postgres, Redis, and Vault** have **no public ingress** — private network only.
- The **public widget holds no operator token** and cannot reach `/admin/*` (§5, FR-029); the cook and
  operator surfaces share the monolith but not the trust level.

**Accepted deviation (v0.1.0):** the prod **Vault** keeps a public HTTPS endpoint *only* for operator
init/unseal/seed — it is sealed-by-default and root-token-gated, and the backend reaches it over the private
network. To be removed once auto-unseal (cloud KMS) lands. Documented in [RUNBOOK.md](RUNBOOK.md) → *Known
deployment deviations*.

## Success-criteria coverage

| Criterion | Mechanism | Gate |
|---|---|---|
| SC-003 100% manipulation refused | deterministic input rail | `redteam.refusal_rate_min: 1.0` |
| SC-004 0 allergen-leaking substitutions | curated map, wall-filtered fail-closed | `tests/unit/test_substitution.py` |
| SC-006 0 allergen recipes on any new path | `recipe_view`→`constraint_guard` choke point | `tests/integration/test_wall_regression.py` |
| SC-007 agent stays within bounds | iteration + token caps, validated tool inputs | `app/agent/loop.py` + agent-tool eval |
| P5 no secret/PII in logs or traces | redaction before log + before span | `redaction.leak_count_max: 0` |
| SC-009 admin endpoints require a valid token | `require_operator`/`require_admin` (per-user JWT, role server-side) | `tests/integration/test_admin.py`, `test_operator_auth.py`, `test_operator_users.py` |
| 005 SC-004 adapters interface-parity, no network | `LLMClient` Protocol + mocked-transport contract test | `tests/contract/test_llm_client.py` |
| 005 SC-005 safety identical under both providers | swap touches only `app/infra/llm`; wall + rails unchanged | `test_wall_regression.py` + `tests/redteam/test_attempts.py` (provider-agnostic via `llm.chat` monkeypatch) |
| 005 SC-006 OpenAI key / pgAdmin pw never leak | `OPENAI_API_KEY` in Vault; pgAdmin pw local-only placeholder; redaction before log + span | `redaction.leak_count_max: 0` |
| 005 SC-008 pgAdmin absent from the deploy | `local` compose profile + backend-only `railway.toml` | `railway.toml` (no `pgadmin` service) |
| 005 FR-018 manual pgAdmin edit can't bypass the wall | guard reads `recipes.allergens` fresh at query time | `tests/integration/test_wall_regression.py` |
| 007 SC-004 zero app secrets in repo/image; Vault-only; datastore creds platform-injected | three-way secrets split (§8a); fail-fast on a missing secret | repo/image key-shape scan + `tests/unit/test_vault.py` |
| 007 FR-001/001a limited public surface | only widget + backend public; dashboard/tracing operator-gated/unadvertised; datastores private (§8b) | deployment topology ([data-model.md](../specs/007-ship-public-deploy/data-model.md) §1) |
| 009 SC-002 zero anonymous access paths | `require_cook` gates every cook endpoint; only `/auth/login` + `/health` open; legacy `X-Profile-ID` → 401 (§6) | `tests/integration/test_cook_auth_gating.py` |
| 009 SC-003 zero self-service registration | no signup route or widget control; only an admin provisions cooks | `tests/integration/test_admin_cooks.py` (no register endpoint) |
| 009 SC-004 cook data isolation | owner key = verified token `sub`; `profiles`/`favorites`/`seen_history` re-keyed to the cook account | `tests/integration/test_cook_data_isolation.py` |
| 009 SC-006 deactivated cook denied | `require_cook` re-loads `sub` + re-checks `is_active` every request | `tests/integration/test_cook_revocation.py` |
| 009 SC-008 wall/grounding no regression on the authenticated path | red-team + wall regression run as the token-authenticated demo cook | `test_wall_regression.py` + `tests/redteam/test_attempts.py` |
