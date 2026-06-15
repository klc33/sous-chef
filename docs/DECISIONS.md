# Decisions

Architecture decision records, organized by the feature that introduced them. Each entry states the
decision, the alternatives weighed, and why — **backed by a concrete number** wherever one exists.
Constitution principles are cited as `P#`.

**The three decisions a reviewer is most likely to ask about, each with its supporting number:**

| Decision | Where | Number |
|---|---|---|
| **ML vs. LLM** for intent routing | **D1** | classifier macro-F1 **0.979** (held-out) |
| **Chunking** strategy for retrieval | **D12** | RAG hit@3 **1.000**, MRR **0.933** (no chunking) |
| **Agent vs. workflow** for handling a turn | **D7** | agent-tool-selection **0.667** (advisory), escalation threshold **0.55** |

---

# Decisions — 003 Intelligent Behavior

Architecture decision records for the intelligent layer (smart retrieval, freshness, planning, the
guarded agent).

## D1 — Intent routing: a trained classical model, not an LLM router

**Decision.** Route every turn with an offline-trained **TF-IDF (word 1–2 grams) + logistic regression**
classifier, served via `joblib` in [app/classifier/predict.py](../app/classifier/predict.py). An LLM
zero-shot router was kept only as a documented baseline, never served.

**Numbers** (held-out eval set `evals/classifier/testset.csv`, 48 rows; full report in
[app/classifier/model_card.md](../app/classifier/model_card.md)):

| Approach | Macro-F1 | Latency | Cost | Determinism |
|---|---|---|---|---|
| **TF-IDF + LogReg (served)** | **0.979** | < ~50 ms local CPU | $0 | deterministic |
| Groq LLM zero-shot (baseline) | not served | ~hundreds of ms / call | per-token | non-deterministic |

**Why.** The classical model matches or beats the LLM baseline on this fixed 6-label set while being free,
fast, deterministic, and **torch-free** to serve (P3, P10). Confidence-based escalation
(`router_confidence_threshold`, default 0.55) sends only low-confidence turns to the agent, so a
misroute degrades cost/quality — **never safety**, because the wall is downstream of routing on every
path. The artifact is SHA-256-pinned in the model card (P5).

**Alternatives rejected.** (a) LLM router — slower, costs per call, non-deterministic, and would make the
F1 gate meaningless. (b) Rules/keywords only — brittle on paraphrase; the labeled dataset + LogReg
generalize better and give a calibrated confidence for escalation.

## D2 — Vectors live in a `pgvector` column, not a separate vector store

**Decision.** Add `recipes.embedding vector(1536)` + an HNSW cosine index via Alembic
[0003_embeddings.py](../alembic/versions/0003_embeddings.py); search with one parameterized SQL query in
[app/repo/recipes.py](../app/repo/recipes.py) `search_by_vector`.

**Why.** pgvector was already enabled in `0001`. One database, one migration, one query layer
(`repo` is the only DB layer, P3) — no extra service to run, secure, or keep in sync (P1, P10). Category +
diet + seen-history are pushed into the same `WHERE` clause as cheap exact pre-filters.

## D3 — Embeddings from a separate hosted provider; Groq is chat-only

**Decision.** Embeddings (`text-embedding-3-small`, dim 1536) come from an OpenAI-compatible endpoint
behind [app/infra/embeddings.py](../app/infra/embeddings.py); Groq serves chat + tool-calling behind the
LLM seam [app/infra/llm/](../app/infra/llm/) (the Groq adapter is
[app/infra/llm/groq.py](../app/infra/llm/groq.py); see **D9** for the provider-agnostic seam that 005
introduced around it). Both keys live in Vault (P4).

**Why.** Groq is chat-only — it has no embeddings endpoint. Splitting the adapters keeps each external
dependency mockable in tests (P3, P4) and gives each path its own rate-limit bucket. The two Groq models
are also split: `groq_model` (`llama-3.1-8b-instant`) for the workflow path and `groq_agent_model`
(`llama-3.3-70b-versatile`) for the bounded agent, where reliable multi-tool calling matters.

## D4 — Over-fetch, then let the wall trim (never `LIMIT 3` before the allergen check)

**Decision.** `search_by_vector` returns an over-fetched candidate **pool** (`retrieval_candidate_pool`,
default 20), and [app/services/user/rag.py](../app/services/user/rag.py) applies the deterministic allergen
wall over the pool before slicing the **top 3** the cook sees.

**Why.** A hard `LIMIT 3` in SQL could under-return when compliant recipes rank deeper than violators.
Over-fetching guarantees 3 wall-cleared cards surface whenever they exist (P-safety: "the wall is the
grade"). The RAG gate therefore measures **hit@3** — what the cook actually sees — not hit@pool.

## D5 — Freshness is one global per-cook seen-set, reset on exhaustion

**Decision.** [app/services/user/freshness.py](../app/services/user/freshness.py) excludes a cook's
seen-history from retrieval and clears it when the compliant pool can no longer supply `k` unseen rows.
Favorites are never recorded into nor suppressed by seen-history.

**Why.** A single set per cook (not per-query) makes "same request twice → zero overlap" hold across
phrasings, and the reset-on-exhaustion guarantees discovery never dead-ends (SC-001). Exempting favorites
keeps saved recipes always reachable.

## D6 — Grounding: the LLM explains real rows; substitutions come from a curated map

**Decision.** Cards/plans/steps render only stored rows; the LLM ranks/phrases **only** the retrieved
recipes and never invents (recipe_explainer / agent_system prompts). Substitutions come from the curated
[app/services/shared/substitutions_data.py](../app/services/shared/substitutions_data.py), wall-filtered —
never generated.

**Why.** "Ground everything" (P-safety, SC-005). A no-match returns an honest empty result, not a
fabricated or constraint-relaxed recipe. A curated substitution map is safer and cheaper than another
model (P10).

## D7 — One bounded tool-calling agent for hard/multi-step intents

**Decision.** [app/agent/loop.py](../app/agent/loop.py) runs a single agent with five schema-validated
tools (`search_recipes`, `get_recipe`, `get_nutrition`, `build_shopping_list`, `substitute_ingredient`),
capped by `agent_max_iterations` (default 5) + a token budget, returning the best safe partial on bound.

**Why.** Exactly one agent (P1); every tool input is Pydantic-validated and every recipe output passes
the `recipe_view` wall choke point (P3, P6, SC-007). Bounds make the loop always terminate; a degraded
tool choice never produces an unsafe recipe because the wall is downstream of the tool.

**Numbers (agent-vs-workflow split).** The route is the **classifier's** call (D1, macro-F1 **0.979**):
high-confidence easy intents take the deterministic **workflow** path; only turns below the escalation
threshold (`router_confidence_threshold`, default **0.55**) plus the inherently hard/multi-step intents
reach the **agent**. So the agent runs on the *minority* of turns by design — the cheap, deterministic
workflow is the common path. Agent quality is tracked by the **advisory** agent-tool-selection eval
(**0.667 = 4/6** on 2026-06-11; no hard threshold — tool choice degrades quality, never safety, SC-007),
and the live **workflow-vs-agent routing split** is instrumented as a Redis counter surfaced on the
operator dashboard (`router.record_decision`). Picking a trained classifier as the router (vs. an LLM
router) keeps this split decision free, fast, and deterministic.

## D8 — Deterministic guardrails before routing and before the reply leaves

**Decision.** [app/guardrails/input_rails.py](../app/guardrails/input_rails.py) screens the untrusted
message with deterministic patterns BEFORE routing (refuse allergen/diet-override outright; strip
injection/jailbreak fragments, serving any safe remainder).
[app/guardrails/output_rails.py](../app/guardrails/output_rails.py) redacts the reply and **re-asserts the
wall** on every recipe before the response leaves and before any Phoenix span.

**Why.** A deterministic rail makes the red-team gate provable and reproducible (P6) — refusal rate is a
hard 1.0 gate, not a model's best guess. The output rail re-asserting the wall is defense in depth: a new
code path that forgets the wall still cannot leak a violating recipe.

# Decisions — 005 Operability & Model Flexibility

## D9 — A provider-agnostic LLM seam: one `Protocol` + a stable facade, swapped by one setting

**Decision.** Replace the single `app/infra/llm_groq.py` module with an `app/infra/llm/` package: a
`base.LLMClient` [Protocol](../app/infra/llm/base.py), a [groq.py](../app/infra/llm/groq.py) adapter (the
prior Groq code moved verbatim — lazy `lru_cache` client, Vault `GROQ_API_KEY`, 429-retry/backoff), a new
[openai.py](../app/infra/llm/openai.py) adapter on the **already-vendored** `openai` SDK, and a
[factory.py](../app/infra/llm/factory.py) `get_client()` selecting by `settings.llm_provider`. The package
[`__init__.py`](../app/infra/llm/__init__.py) is the stable module-level **facade** exposing `chat(...)`, so
the two call sites ([services/user/rag.py](../app/services/user/rag.py),
[app/agent/loop.py](../app/agent/loop.py)) and the tests change **only their import** (`llm_groq` → `llm`).
Provider is chosen at startup by `LLM_PROVIDER` (`groq` default | `openai`); an unknown value fails fast at
settings load via a `Literal` type. Provider keys both come from Vault (`GROQ_API_KEY` / `OPENAI_API_KEY`).

**Why.** The existing `llm_groq.chat(messages, *, tools, max_tokens, model)` signature *was already* the
desired seam, so `groq.py` is a near-verbatim move and the two callers change one line each (P1, P7). Both
SDKs are OpenAI-compatible and return the **same response shape** (`choices[0].message.{content,tool_calls}`,
`usage.total_tokens`) the agent already reads, so the tool-calling contract and token accounting are
**identical across providers with zero translation** — no custom DTO, no `loop.py` change (Decision 2). A
`typing.Protocol` (not an ABC) keeps the contract a pure structural check the
[contract test](../tests/contract/test_llm_client.py) verifies with a mocked transport and **no network**
(P4, SC-004). Reusing the vendored `openai` SDK adds **no new runtime dependency and no torch** (P10,
FR-017/SC-009); the OpenAI adapter mirrors Groq's bounded retry so the two behave the same under throttling
(Decision 4). Safety is **provider-independent by construction**: the swap touches only `app/infra/`, never
the deterministic wall or guardrails, and the wall-regression + red-team suites prove this under the active
provider (P-safety, SC-005). Default `groq` preserves current behavior with zero config change (P5).

**Observability (FR-009a / SC-005a).** The facade attaches `llm.provider` / `llm.model` /
`llm.total_tokens` to the active OpenTelemetry span after each call, wrapped in `contextlib.suppress` so a
tracing hiccup never breaks a turn. Because the attribution is set at the **single** seam every generation
flows through, Groq and OpenAI emit identical attributes — parity is by construction, not duplicated code.

**Frozen judge stays Groq-pinned.** The report-only RAG judge in
[evals/run_evals.py](../evals/run_evals.py) instantiates `GroqClient` **directly** (not the swappable
facade) against a pinned model id, so its non-deterministic quality scores stay comparable run-to-run even
when the app itself is running on OpenAI. Same for the documented Groq zero-shot baseline in `ml/`.

**Alternatives rejected.** (a) Keep `llm_groq.py` + add `llm_openai.py` and branch at each call site —
spreads the provider choice across callers, violates the one-seam goal and P3. (b) A provider-neutral
`ChatResult` DTO mapping both SDKs — needless for this MVP since both SDKs already agree; would force a
`loop.py` change for no behavior gain (noted as future hardening only if a non-compatible third provider
appears). (c) `LLM_PROVIDER` also switching embeddings — out of scope and risky against the migration-pinned
`vector(1536)` column; embeddings keep their own provider/key (Decision 7).

# Decisions — 006 Corpus Data Quality

## D10 — Image grounding: a generic per-category placeholder, not a borrowed/stock/AI photo

**Decision.** When a recipe has no source `image_url`, the cook-facing surfaces render one of five
**committed generic per-category placeholder SVGs** (`hot_drink` / `cold_drink` / `breakfast` / `lunch` /
`dinner`) — never a stock, borrowed, or AI-generated photo standing in for the dish. The selection is a
pure client-side helper [widget/src/lib/images.js](../widget/src/lib/images.js) `imageFor(recipe) →
{ src, alt }`: `src` = `recipe.image_url` else `placeholderFor(recipe.category)`; `alt` = `recipe.title`.
Both [RecipeCard](../widget/src/components/RecipeCard.jsx) and
[RecipeDetail](../widget/src/components/RecipeDetail.jsx) wire an `<img onError>` that swaps a failed
source-photo load to the same category placeholder, so a 404/blocked host degrades to the placeholder
rather than a broken-image icon.

**Why.** "Ground everything" (P-safety, golden rule #2): a real photo is the dish; a generic category
icon is honestly generic; a borrowed/stock/AI photo would *assert* a false fact about what the cook will
cook. The placeholders are **committed static assets** — no runtime image service, no third-party fetch,
no key, and no schema change (the persisted `recipes.image_url` already carries nullability). Because
every fixed `Category` has a committed asset, placeholder resolution **can never fail**; that precondition
is the one thing pinned by a CI test ([tests/unit/test_image_placeholders.py](../tests/unit/test_image_placeholders.py))
since the widget has no JS unit-test runner (and plan.md adds none — the `imageFor`/`onError` render path
is verified manually per quickstart §3).

**Alternatives rejected.** (a) A stock/Unsplash or AI-generated photo per recipe — fabricates the dish's
appearance, violating grounding, and adds a runtime fetch + dependency for no honest gain. (b) A
server-side image-resolution service / new `placeholder_url` column — needless: selection is deterministic
from the existing category, so it belongs in the client with zero new infrastructure (P3, P10). (c) Keep
today's blank `card__img--placeholder` div with `alt=""` — leaves the detail view image-less and gives
screen readers nothing; naming the recipe in `alt` is strictly more honest and accessible.

# Decisions — 007 Ship to a Public URL

## D11 — Tracing backend is pluggable: self-hosted Phoenix (dev) or LangSmith Cloud (prod), one selector

**Decision.** Tracing keeps a single OTLP/HTTP export path but the destination is chosen by one setting,
`TRACING_PROVIDER` ([app/config.py](../app/config.py)): `phoenix` (self-hosted collector — the default and
local-dev path) or `langsmith` (LangSmith Cloud OTLP ingest). `_exporter_config`
([app/infra/tracing.py](../app/infra/tracing.py)) resolves the endpoint + auth: Phoenix uses the collector
base with no headers; LangSmith uses `…/otel/v1/traces` with `x-api-key` (the **Vault** secret
`LANGSMITH_API_KEY`) + `Langsmith-Project`. Both run through the **same `_RedactingSpanExporter`**, so
redaction-before-export (golden rule #5) holds for either destination. Selection/auth is best-effort: a
LangSmith provider with no Vault key (or a Phoenix provider with no endpoint) resolves to `None` →
tracing off, never a startup failure (Decision 7).

**Why.** The deploy host (Railway free/trial) caps the project at 5 services, and a self-hosted Phoenix
needs a 6th. LangSmith Cloud is a hosted OTLP sink, so prod gets real tracing with **no extra service** —
no slot, no widget move, no plan upgrade. Keeping Phoenix as the default preserves the self-hosted,
network-free local dev story (and the offline `make up` stack) unchanged; only `TRACING_PROVIDER=langsmith`
opts a deploy into the cloud sink. The change is provider-shaped like D9 (LLM seam): one setting, one
config resolver, zero changes to the request path or the redaction guarantee.

**Cost / deviation.** This is a deliberate deviation from "self-hosted tracing" (CLAUDE.md): with
`langsmith`, spans egress to a third-party cloud. The redacting exporter still strips secrets/PII **before**
export (the `redaction.leak_count_max: 0` gate covers the span path), so no secret leaves — but it is
external egress, and the LangSmith free tier caps monthly trace volume. The key stays in Vault (golden
rule #4), never in env/image.

**Alternatives rejected.** (a) **Self-hosted Phoenix on Railway** — the original design (T017c), blocked by
the service cap; would need a paid plan or moving the widget to a free static host (kept as the documented
fallback in [dashboardxphoneix.md](../dashboardxphoneix.md) §C.2). (b) **Phoenix Cloud / Arize** — same
auth-header requirement as LangSmith, so no simpler; LangSmith chosen for its free developer tier + OTLP
support. (c) **No prod tracing** — acceptable (tracing is non-blocking) but loses the observability story a
reviewer expects; the selector makes real tracing essentially free to enable.

## D12 — No chunking: one embedding vector per whole recipe

**Decision.** Recipes are **not chunked**. Each recipe is embedded as a single document (title +
ingredients + steps, joined) into one `recipes.embedding vector(1536)` row (D2), and retrieval ranks whole
recipes by cosine similarity. There is no splitter, no per-chunk vector table, and no chunk-to-document
reassembly step.

**Numbers.** On the labeled golden set (`evals/rag/golden.yaml`) against the embedded corpus (2,224
recipes) the whole-recipe vectors score **hit@3 = 1.000 (10/10)** and **MRR = 0.933** (2026-06-11) — the
cook reliably sees an ideal recipe in the top-3, so chunking would add machinery and a reassembly seam for
no measurable retrieval gain. Both floors are pinned at a conservative **0.80** in
[eval_thresholds.yaml](../eval_thresholds.yaml) and never weakened (golden rule #6).

**Why.** Chunking exists to retrieve a *passage* from a long document; a recipe is **already the unit of
retrieval and of the answer** — the cook wants a whole recipe, not a fragment of one, and the detail view
renders the recipe's stored steps **verbatim** (D6, grounding). A recipe is short enough to embed whole, so
one vector per recipe keeps the index 1:1 with the `recipes` table — the over-fetch-then-wall-trim pipeline
(D4) operates on whole rows, and freshness/category/diet pre-filters (D5) are plain `WHERE` clauses on the
same row (P1, P3). It also keeps the embedding space aligned 1:1 with the committed seed corpus
(`recipes.jsonl` ↔ `embeddings.npy`, one row each), which is what makes the seed artifact reproducible.

**Alternatives rejected.** (a) **Per-step or fixed-window chunking** — would return a step out of context,
fight grounding (the cook needs the whole recipe), and force a chunk→recipe reassembly + dedup layer for no
hit@3 improvement on this corpus. (b) **A separate chunk vector table** — extra schema, extra writes, extra
join, and a second thing to keep in sync with the seed corpus; rejected as unjustified complexity (P1,
P10).

# Decisions — 008 Admin-Managed Operator Accounts

## D13 — Operator auth is per-user accounts + a signed JWT, not the single shared token

**Decision.** Replace 004's single hardcoded operator (one `OPERATOR_USERNAME` + one Vault
`OPERATOR_PASSWORD_HASH`) and the **static shared `ADMIN_API_TOKEN`** with **named, admin-managed accounts
authenticated by a signed JWT**. Login ([app/api/admin/auth.py](../app/api/admin/auth.py)) verifies a
username/password against the durable `operator_accounts` table (bcrypt hashes) and mints a short-lived
**HS256 JWT** `{sub, role, iat, exp}` ([app/core/security.py](../app/core/security.py), signed with the Vault
`JWT_SIGNING_KEY`, TTL `JWT_TTL_MINUTES` = 8h). The backend verifies that token and enforces the **admin-vs-user**
split server-side in [app/api/admin_deps.py](../app/api/admin_deps.py) — `require_operator` (any valid, active
account) vs `require_admin` (`role == 'admin'`). An admin provisions/lists/deactivates/reactivates/resets
accounts from a dashboard **Users** page; there is **no self-service sign-up**; a first admin is
**bootstrapped** at startup from Vault (`BOOTSTRAP_ADMIN_PASSWORD`) on an empty table (idempotent); the **last
active admin can never be deactivated**. The three retired keys (`OPERATOR_USERNAME`, `OPERATOR_PASSWORD_HASH`,
`ADMIN_API_TOKEN`) are removed from config, the seed script, and the Vault keyspace
([contracts/secrets-keyspace.md](../specs/008-admin-managed-operator-accounts/contracts/secrets-keyspace.md)).

**Why.** The requirement is to **grant/revoke access per person with an admin-vs-user role** — the static
shared token structurally **cannot identify who is acting or carry a role**, which is the whole point. A
per-user JWT makes the backend know *who* and *what role* on every call, enforces the role **in code** (not
just hidden in the UI, FR-011), and gives **immediate revocation**: `require_operator` re-loads the `sub`
account and re-checks `is_active` every request, so a deactivated account is denied on its next call without
waiting for the token to expire. Auth is **constant-time** with a dummy-verify on unknown users and a single
**generic 401**, so there is no account enumeration (FR-012). bcrypt hashes stay in the **DB row, not Vault** —
a hash is one-way and not a recoverable secret, so Vault holds only the JWT signing key + bootstrap password.
This is **operator-console** auth (constitution 2.0.0 sanctions it, already in the stack via
`streamlit-authenticator`); the cook/end-user path stays the passwordless `X-Profile-ID` and is untouched
(SC-007). PyJWT + bcrypt are tiny, torch-free libs in the **backend** group only (the dashboard image never
hashes or mints — it just stores the JWT it is handed), so no image bloats and golden rule #3 holds.

**Alternatives rejected.** (a) **Keep the single shared `ADMIN_API_TOKEN`** — fails the actual requirement
(no per-person grant/revoke, no role, no accountability). (b) **A home-rolled HMAC bearer** — would
re-implement JWT, worse and unreviewed. (c) **Store the role only in a client cookie** — forgeable; the role
must be a signed claim the backend verifies. (d) **A server-side session store (Redis)** — adds a stateful
dependency for the session when the signed JWT *is* the session (no datastore needed, and Redis is already
optional here, D-007); deactivation immediacy is instead handled by the per-request `is_active` re-check.

# Decisions — 009 Admin-Managed Cook Accounts

## D14 — End-user (cook) identity is an admin-provisioned account behind total login gating, replacing the passwordless profile-ID

**Decision.** Retire the passwordless **`X-Profile-ID`** cook identity and replace it with a **named,
admin-provisioned cook account** behind **total login gating**. A cook signs in on the React widget (a
professional login screen, **no self-signup**) via `POST /auth/login`
([app/api/user/auth.py](../app/api/user/auth.py)), which verifies a username/password against a new
`cook_accounts` table (bcrypt) and mints a short-lived **HS256 JWT** `{sub, typ:"cook", iat, exp}`
([app/core/security.py](../app/core/security.py), signed with the Vault **`COOK_SESSION_KEY`**, TTL
`COOK_JWT_TTL_MINUTES` = 8h). `require_cook` ([app/api/deps.py](../app/api/deps.py)) gates **every** cook
endpoint (`/chat`, `/recipes*`, `/favorites*`, `/profile`); only `/auth/login` + `/health` are open. The cook
account id (the verified token `sub`) becomes the **owner key** threaded exactly where `X-Profile-ID` was, so
`profiles`/`favorites`/`seen_history` are re-keyed to the account (Alembic `0005`, with a `cook_accounts` FK)
and each cook sees only their own data. An operator **admin** (008) creates/lists/deactivates/reactivates/
resets cooks from a dashboard **Cooks** page (`/admin/cooks`, `require_admin`); a **seeded demo cook** lets CI
and the live demo authenticate (no bypass). Enabled by **constitution 2.0.0**, which sanctions
admin-provisioned end-user auth while still prohibiting self-service signup.

**Why.** The headline value is *a user account connected to the user's favorites/profile* — the passwordless
header structurally **cannot** authenticate anyone: any client could send any id and read/write that "owner's"
data, so there was no real ownership, no accountability, and no way to revoke access. A signed cook session
makes ownership **authenticated** (the owner is a verified claim, never a client-supplied header/body),
gives **immediate revocation** (`require_cook` re-loads the `sub` cook and re-checks `is_active` every
request, so a deactivated cook is denied on its next call), and brings the formerly-**open** chat under
**auth** — now exercised by the safety gates on the authenticated path. The cook domain is **isolated from the
operator domain** (FR-013): a **separate** Vault signing key **and** a `typ:"cook"` claim mean an 008 operator
token can never be replayed as a cook session, or vice-versa. Reusing 008's `core/security` (bcrypt + JWT)
adds **no new dependency**; bcrypt hashes stay in the **DB row, not Vault** (one-way, not a recoverable
secret). **Total gating** (no anonymous mode) is what keeps the wall/grounding/redaction/red-team gates
honest — even CI authenticates as a real cook rather than a bypass (SC-008).

**Alternatives rejected.** (a) **Keep `X-Profile-ID`** — no authentication, no ownership, no revocation; the
whole point is a real account. (b) **Self-service signup** — explicitly prohibited (constitution 2.0.0);
cooks are admin-provisioned, mirroring 008. (c) **One shared signing key/claim for both operator and cook
tokens** — blurs the two trust domains and widens blast radius if either key leaks; FR-013 demands isolation.
(d) **Migrate the existing anonymous rows to accounts** — an anonymous `profile_id` UUID has no owner to map
to, so this would invent owners; dropping them on the `0005` re-key (FR-015, an **irreversible** wipe — back
up first) is the honest fresh start. (e) **A server-side session store** — the signed JWT *is* the session
(no datastore); deactivation immediacy comes from the per-request `is_active` re-check, as in 008.
