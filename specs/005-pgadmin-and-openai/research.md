# Phase 0 Research: Operability & Model Flexibility

All decisions below resolve the design unknowns for the LLM seam and the pgAdmin service. There were no
open `NEEDS CLARIFICATION` markers from the spec (the observability question was resolved in
`/speckit-clarify`, Session 2026-06-13).

## Decision 1 — Seam shape: a Protocol package + a stable facade

**Decision**: Replace `app/infra/llm_groq.py` with a package `app/infra/llm/` containing `base.py` (the
`LLMClient` Protocol), `groq.py`, `openai.py`, `factory.py` (`get_client()`), and `__init__.py` (the
facade that exposes `chat(...)`). Call sites import `from app.infra import llm` and call `llm.chat(...)`.

**Rationale**: The existing `llm_groq.chat(messages, *, tools=None, max_tokens=None, model=None)` signature
is *already* the desired seam signature, so `groq.py` is a near-verbatim move (preserving the lazy
`lru_cache` client + 429 retry). A package whose `__init__.py` is the facade lets `app.infra.llm.chat` be
the single import the whole app and the tests depend on (the user's explicit "ONE seam" requirement),
while `get_client()`/adapters stay swappable behind it. This is the minimal structure that satisfies
FR-001/FR-003 (no call-site changes beyond the import).

**Alternatives considered**:
- *Keep `llm_groq.py` and add `llm_openai.py`, branch at each call site* — rejected: spreads the provider
  choice across call sites, violates FR-001/FR-003 and Principle III.
- *Abstract base class instead of `typing.Protocol`* — rejected: a Protocol is lighter, needs no
  inheritance from the SDK-wrapping adapters, and makes the contract test a pure structural check.

## Decision 2 — The normalized response & tool-call contract

**Decision**: Both adapters return the **OpenAI-style response object** the agent already consumes:
`response.choices[0].message.content`, `response.choices[0].message.tool_calls[*].{id, function.name,
function.arguments}`, and `response.usage.total_tokens`. No custom DTO is introduced.

**Rationale**: Groq's SDK is OpenAI-compatible and `app/agent/loop.py` already reads exactly these
attributes (`_usage_tokens` reads `usage.total_tokens`; `_assistant_history_entry` reads
`message.tool_calls` and `call.function.{name,arguments}`). The OpenAI SDK returns the identical shape
natively. So "the same native tool-calling shape" (FR-004/FR-007) is satisfied with **zero** translation
for the happy path, and the contract test asserts both adapters expose these same attributes. Keeping the
raw response (rather than a custom DTO) means the bounded loop, the constraint guard, and the history
serialization are untouched.

**Alternatives considered**:
- *Define a provider-neutral `ChatResult` dataclass and map both SDKs into it* — rejected for this MVP:
  it would force edits to `loop.py`'s response handling (against "no call-site changes") for no behavior
  gain, since both SDKs already agree. Noted as a possible future hardening if a third, non-compatible
  provider is ever added.

## Decision 3 — Provider selection + fail-fast on a bad value

**Decision**: Add `llm_provider: Literal["groq", "openai"] = "groq"` to `app/config.py` (env `LLM_PROVIDER`).
`get_client()` switches on it. An unknown value fails at settings load (pydantic rejects a non-Literal
value with a clear error); a missing value uses the `groq` default. A missing **secret** for the selected
provider fails fast at first use via the existing `VaultAdapter.get()` → `StartupConfigError`.

**Rationale**: Using `Literal` makes "fail fast with a clear error on an unknown value" (FR-005, SC-003)
free and declarative — no hand-rolled validation. Default `groq` preserves current behavior with no config
change (Assumption: default provider is Groq). The Vault-miss path reuses the established
`StartupConfigError` pattern from `embeddings.py`/`vault.py`.

**Alternatives considered**:
- *Plain `str` with a manual check in the factory* — rejected: duplicates what `Literal` gives for free and
  risks an inconsistent error message.

## Decision 4 — OpenAI adapter: reuse the vendored SDK, mirror Groq's resilience

**Decision**: `openai.py` builds an `openai.OpenAI(api_key=vault.get("OPENAI_API_KEY"))` client lazily via
`lru_cache` (mirroring `embeddings.py` and `groq.py`), and implements the same `chat(...)` signature. It
honors `settings.openai_model` (default) and accepts the agent's `settings.openai_agent_model` override.
It retries transient rate-limit errors with bounded backoff, mirroring the Groq adapter's 429 handling.

**Rationale**: The `openai` SDK is already a backend-group dependency (used by `embeddings.py`), so this
adds **no new runtime dependency** (FR-017, SC-009) and no `torch`. Reusing the lazy-cached-client +
Vault-read pattern keeps both adapters structurally identical and easy to reason about. Reliability parity
(bounded retry) was the item deferred from `/speckit-clarify` as plan-level; resolving it here as "mirror
Groq's retry budget" keeps the two providers behaviorally comparable without over-engineering.

**Alternatives considered**:
- *No retry in the OpenAI adapter* — rejected: would make the two providers behave differently under
  throttling, weakening the "swap changes nothing observable" contract.
- *A shared retry decorator across both adapters* — deferred: nice, but a small duplication is simpler than
  a new abstraction for two call sites (Principle I); can be factored later if a third provider appears.

## Decision 5 — Observability parity (resolves the clarification)

**Decision**: In the facade `chat(...)`, after a successful provider call, attach best-effort attributes to
the **current** OpenTelemetry span: `llm.provider`, `llm.model`, and `llm.total_tokens` (read from
`response.usage.total_tokens`, default 0). Setting is wrapped in `contextlib.suppress(Exception)` so a
tracing hiccup never breaks a turn (Decision 7 posture in `tracing.py`). Redaction already runs on all span
attributes before export, so these non-secret attributes pass through unchanged and no secret is added.

**Rationale**: The approved clarification requires per-turn token/cost attribution under **both** providers
(FR-009a, SC-005a). Today `tracing.py` emits only an HTTP request span and token usage is read solely for
the agent budget — it is **not** on a span. The minimal way to satisfy parity *and* the constitution's
"latency + token cost" intent is to emit the token count (and the active provider/model) as span attributes
at the single seam every generation flows through. Because the attribute is set at the facade, it is
provider-agnostic by construction — Groq and OpenAI get identical attributes — which is exactly the parity
the clarification demands and is trivially verifiable.

**Alternatives considered**:
- *Interpret "parity" as merely "don't lose the `usage` field"* (no span attribute) — rejected: the
  clarification explicitly says "the same per-turn token/cost attribution," and there is currently no
  attribution on a span at all, so "the same" must mean "present and identical," which requires emitting it.
- *Add a dedicated child span per LLM call* — deferred as heavier than needed for this feature; a single
  attribute on the active request span satisfies the requirement with far less surface. Can be upgraded
  later without changing the seam's public contract.

## Decision 6 — pgAdmin: `local` profile + pre-provisioned connection; `make up` activates it

**Decision**: Add `pgadmin` (`dpage/pgadmin4`) to `docker-compose.yml` with `profiles: ["local"]`,
`depends_on: postgres`, a published local port (`5050:80`), `PGADMIN_DEFAULT_EMAIL`/`PGADMIN_DEFAULT_PASSWORD`
sourced from local-only env (in `.env.example`, never Vault), and a mounted `docker/pgadmin/servers.json`
pre-provisioning the Postgres server connection so it is usable on first boot. Change `make up` to activate
the profile (`docker compose --profile local up --build`) and add `make pgadmin` to print/open the local
URL. pgAdmin is **not** added to `railway.toml` / the Railway service set.

**Rationale**: This reconciles the user's explicit "under a `local`/dev profile" instruction with the
spec's "one command brings up pgAdmin alongside the stack": CLAUDE.md already designates **`make up`** as
the canonical one-command bring-up, so activating the profile there means the documented path brings up
pgAdmin, while a bare `docker compose up` (no profile) deliberately omits it — an explicit local-only
signal. Exclusion from production is doubly guaranteed: Railway deploys via `railway.toml` (backend only;
compose services are not Railway services) **and** the `local` profile would gate it out of any future
compose-based deploy (FR-015, SC-008). The pre-provisioned `servers.json` satisfies "works on first boot
without manual connection setup" (FR-013, SC-007).

> **Resolved (analysis F1)**: the spec was reconciled to this decision — FR-012, SC-007, the Assumptions,
> and a Clarifications entry now state the canonical bring-up is `make up` (which activates the `local`
> profile) and that a bare `docker compose up` without the profile intentionally omits pgAdmin. The
> always-on (no-profile) variant remains the documented fallback if strict bare-`docker compose up`
> inclusion is ever preferred.

**pgAdmin password connection note**: pgAdmin's *master* password (login to pgAdmin itself) is the local
convenience credential. The **Postgres** password it uses to connect is the existing dev Postgres password
(`postgres`), already present in compose; `servers.json` provides connection metadata (host/port/db/user)
but pgAdmin still prompts for/stores the DB password locally — acceptable for a local dev tool. No secret
is added to Vault, code, image, or `.env.example` beyond the local-only pgAdmin master credentials.

**Alternatives considered**:
- *No profile (always-on local service)* — viable and matches the bare-`docker compose up` wording, but
  discards the explicit local-only profile signal the user asked for; kept as the documented fallback.
- *Read-only pgAdmin* — rejected: the spec explicitly says inspect **and repair**, so read-write is
  intended; the wall still enforces safety at query time regardless of manual edits (FR-018).

## Decision 7 — Embeddings stay out of the seam

**Decision**: `LLM_PROVIDER` governs **chat/agent generation only**. `app/infra/embeddings.py` (the
separate OpenAI-compatible embeddings provider) is untouched and continues to resolve `EMBEDDINGS_API_KEY`
independently.

**Rationale**: The spec scopes the seam to "chat/agent generation" (FR-001 covers text generation +
tool-calling; embeddings are neither). Embeddings already run on an OpenAI-compatible provider via its own
config/key, and the corpus is embedded once at ingestion — coupling embeddings to the chat provider would
risk a dimension/model mismatch against the pinned `vector(1536)` column for no benefit. Documented as an
Assumption in the spec.

**Alternatives considered**:
- *Make `LLM_PROVIDER=openai` also switch embeddings to OpenAI* — rejected: out of scope, and risky given
  the migration-pinned embedding dimension.
