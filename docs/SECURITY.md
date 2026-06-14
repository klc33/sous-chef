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
- **Identity**: the cook is a passwordless `X-Profile-ID` header used only for favorites + seen-history;
  the owner/tenant is never taken from the request body.

## 5. Operator auth — two Vault-sourced boundaries (004)

The operator surface (the `/admin/*` API + the Streamlit dashboard) adds **no end-user auth system**; it is
guarded by **two boundaries, both keyed from Vault** (research R3). Every operator secret lives in Vault KV
v2 `secret/sous-chef` — never in `.env`, code, or an image (golden rule #4). The keys are seeded by
[scripts/seed_vault.sh](../scripts/seed_vault.sh) (dev placeholders out of the box; real values forwarded
from the operator's shell) and read at startup via [app/config.py](../app/config.py) /
[app/infra/vault.py](../app/infra/vault.py); the backend **fails fast** if `ADMIN_API_TOKEN` is missing.

| Boundary | Secret (Vault key) | Mechanism |
|---|---|---|
| **Human → dashboard** | `OPERATOR_PASSWORD_HASH` (bcrypt), `DASHBOARD_COOKIE_KEY` | `streamlit-authenticator` cookie login; the cookie is signed with the Vault key so a **refresh stays logged in** (FR-028). The hash is pre-computed — the dashboard never sees a plaintext password and `auto_hash=False` prevents re-hashing. |
| **Dashboard → backend** | `ADMIN_API_TOKEN` (shared bearer) | The dashboard attaches `Authorization: Bearer <token>` on every `/admin/*` call; [app/api/admin_deps.py](../app/api/admin_deps.py) `require_operator` validates it (401 missing/malformed, 403 wrong token) and gates every admin route. |

**Boundary properties**:

- The **public widget holds no token** and cannot reach `/admin/*` (FR-029) — it only calls the
  profile-scoped user endpoints. The admin and cook surfaces share the monolith but not the trust level.
- The dashboard reads Vault **over HTTP** (it carries `httpx`, not `hvac`) and **never imports the `app`
  package** — it is the dashboard image's only secrets touchpoint, keeping that image lean and decoupled.
- Admin endpoints stay read-only inspection / on-demand eval runs (corpus browse, `evals/run`, metrics);
  the corpus browse goes through `repo/recipes` (parameterized, P3), never raw SQL.

## 6. Operability surface — the LLM seam & pgAdmin (005)

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

## 7. Deployment security — the secrets split & the limited public surface (007)

Shipping to a public URL adds two security properties, both enforced by topology rather than trust.

### 7a. The three-way secrets split (FR-004/005/006, SC-004)

Every value the running system needs falls into **exactly one** of three homes — **nothing secret is ever a
Railway variable, and no secret is ever in the repo, an image, or `.env`** (golden rule #4). Full keyspace
in [contracts/secrets-keyspace.md](../specs/007-ship-public-deploy/contracts/secrets-keyspace.md) and
[data-model.md](../specs/007-ship-public-deploy/data-model.md) §2.

| Home | What lives here | Examples | Rule |
|---|---|---|---|
| **Vault** (`secret/sous-chef`, KV v2) | **all application secrets** — the only home | `GROQ_API_KEY`, `EMBEDDINGS_API_KEY`, `OPENAI_API_KEY`, `LANGSMITH_API_KEY`, `OPERATOR_PASSWORD_HASH`, `DASHBOARD_COOKIE_KEY`, `ADMIN_API_TOKEN`, `app_secret` | seeded once by [scripts/seed_vault.sh](../scripts/seed_vault.sh) into the persistent prod Vault; read at startup |
| **Platform-injected** | managed **datastore credentials** | `POSTGRES_URL` (Postgres plugin), `REDIS_URL` (Redis plugin, optional) | provided by Railway's managed plugins — never hand-set, never in Vault |
| **Railway variables** | **bootstrap + non-secret only** | `ENV`, `VAULT_ADDR`, `VAULT_TOKEN`, `TRACING_PROVIDER`, `LANGSMITH_PROJECT` (name), `WIDGET_ORIGINS`, `BACKEND_ADMIN_URL`, `OPERATOR_USERNAME`, `VITE_API_BASE` | non-secret config selectors |

The **one** deliberate nuance: `VAULT_TOKEN` is a real `hvs.`-shaped token living as a Railway variable.
This is **contract-allowed by design** — it is the chicken-and-egg bootstrap credential the backend needs
*to reach* Vault, so it cannot itself live in Vault. It is bootstrap, not an application secret.

**Proven, not asserted (SC-004):** a repo + image key-shape scan (`gsk_…` / `sk-…` / `hvs.…` / hardcoded
`*_API_KEY=<literal>`, excluding test fixtures + prose) returns **zero real secrets**; the only hits are the
deliberately-fake redaction fixtures. **Fail-fast** is locked by [tests/unit/test_vault.py](../tests/unit/test_vault.py):
remove any required secret (or hit an unseeded Vault path) and `VaultAdapter.load_secrets()` raises
`StartupConfigError` with a seed-pointing message — the backend never boots silently degraded (FR-004/FR-014).

### 7b. The limited public surface (FR-001/FR-001a)

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
| SC-009 admin endpoints require a valid token | `require_operator` (Vault admin token) | `tests/integration/test_admin.py` |
| 005 SC-004 adapters interface-parity, no network | `LLMClient` Protocol + mocked-transport contract test | `tests/contract/test_llm_client.py` |
| 005 SC-005 safety identical under both providers | swap touches only `app/infra/llm`; wall + rails unchanged | `test_wall_regression.py` + `tests/redteam/test_attempts.py` (provider-agnostic via `llm.chat` monkeypatch) |
| 005 SC-006 OpenAI key / pgAdmin pw never leak | `OPENAI_API_KEY` in Vault; pgAdmin pw local-only placeholder; redaction before log + span | `redaction.leak_count_max: 0` |
| 005 SC-008 pgAdmin absent from the deploy | `local` compose profile + backend-only `railway.toml` | `railway.toml` (no `pgadmin` service) |
| 005 FR-018 manual pgAdmin edit can't bypass the wall | guard reads `recipes.allergens` fresh at query time | `tests/integration/test_wall_regression.py` |
| 007 SC-004 zero app secrets in repo/image; Vault-only; datastore creds platform-injected | three-way secrets split (§7a); fail-fast on a missing secret | repo/image key-shape scan + `tests/unit/test_vault.py` |
| 007 FR-001/001a limited public surface | only widget + backend public; dashboard/tracing operator-gated/unadvertised; datastores private (§7b) | deployment topology ([data-model.md](../specs/007-ship-public-deploy/data-model.md) §1) |
