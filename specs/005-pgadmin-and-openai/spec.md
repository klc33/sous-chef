# Feature Specification: Operability & Model Flexibility — pgAdmin + a Provider-Agnostic LLM Seam

**Feature Branch**: `005-pgadmin-and-openai`

**Created**: 2026-06-13

**Status**: Draft

**Input**: User description: "Add operability and model flexibility to Sous-Chef without growing the
stack or touching safety. A pgAdmin web UI wired to the existing Postgres so the operator can
inspect/repair the corpus, favorites, and seen-history visually instead of raw psql — LOCAL/DEV only,
never deployed. A provider-agnostic LLM seam: the chat/agent generation provider is selectable between
Groq (default) and OpenAI by ONE setting (e.g. LLM_PROVIDER), with zero call-site changes; both providers
expose the same `chat(messages, tools?, max_tokens?, model?)` contract and the same native tool-calling
shape, so the router, RAG explainer, and bounded agent are unaware of which one is active. Operability:
a junior operator must see and fix data on demo day without memorizing SQL. Resilience & portability: a
single hosted LLM is a single point of failure and a single bill; a clean seam lets you fail over, A/B
tool-call reliability and cost, and avoid lock-in — at no extra dependency, since the `openai` SDK is
already vendored for embeddings. The OpenAI API key is read from Vault, exactly like GROQ_API_KEY. The
tool-calling contract (tool specs, validated tool inputs, bounded loop, wall) is identical across
providers. No torch; no new runtime Python dependency for OpenAI chat; pgAdmin is local-only; the wall
and guardrails stay deterministic and provider-independent; prompts stay in `prompts/` and unchanged by
the swap."

## Overview

This feature adds two operator-facing capabilities to the existing Sous-Chef monolith **without growing
the production stack or changing any safety behavior**:

1. **Model flexibility** — the LLM generation provider that powers the router fallback, the RAG
   explainer, and the bounded meal-plan agent becomes selectable between **Groq (default)** and
   **OpenAI** by a single configuration value. Every call site that generates text or tool calls goes
   through one seam, so swapping providers requires no code change and changes neither the behavior
   contracts nor the safety guarantees.

2. **Operability** — a **pgAdmin** database UI is added to the local development stack so the operator can
   visually browse and repair the corpus, favorites, and seen-history instead of memorizing `psql`. It is
   a local/dev convenience only and is explicitly excluded from the deployed (Railway) environment.

Both capabilities are deliberately scoped to *not* touch the safety wall, the grounding rules, the
guardrails, or the prompts. The constraint guard and guardrails remain deterministic code and are
provider-independent; the swap is invisible to them.

## Clarifications

### Session 2026-06-13

- Q: Must provider parity include observability (Phoenix tracing + token/cost), or is tracing best-effort
  for the new provider in this feature? → A: Parity **includes** observability — both providers MUST emit
  the same Phoenix spans and per-turn token/cost attribution, verified as part of acceptance.
- Q: How is pgAdmin brought up vs. excluded — bare `docker compose up` or a profile? → A: pgAdmin lives
  under a **local compose profile** that the canonical `make up` activates; a bare `docker compose up`
  without the profile omits it. Exclusion from the deploy is doubly ensured (profile + Railway deploying
  only the backend). [resolves analysis finding F1]
- Q: Is the pgAdmin login password in `.env`/`.env.example` an acceptable exception to "secrets live in
  Vault"? → A: Yes — it is a **sanctioned local-dev exception**: a throwaway, placeholder, never-deployed
  convenience credential (not an application/data secret), documented as such in `docs/SECURITY.md`.
  [resolves analysis finding C1]

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Swap the LLM provider by one setting (Priority: P1)

The operator changes a single configuration value to select which hosted LLM provider serves generation
(Groq or OpenAI) and restarts the service. Every cook-facing behavior that uses the LLM — semantic
search explanations, nutrition answers, ingredient substitutions, and multi-step meal planning with tool
calls — continues to work identically, with no code change between the two providers and no change to the
safety wall or guardrails.

**Why this priority**: A single hosted LLM is a single point of failure and a single bill. A clean,
config-selectable seam removes vendor lock-in, enables failover when one provider has an outage, and lets
the operator A/B tool-call reliability and cost between providers. This is the higher-value, higher-risk
half of the feature because it touches the generation path that every intelligent behavior depends on —
so the contract that "swapping changes nothing observable except the provider" must be proven.

**Independent Test**: Set the provider to OpenAI, restart, and run the full demo (search, nutrition,
substitution, a meal plan); then flip back to Groq and run it again. Both runs complete end-to-end with
identical behavior contracts and no source change in between. A contract test asserts both adapters
satisfy the same interface and emit tool calls in the same shape.

**Acceptance Scenarios**:

1. **Given** the provider setting is `groq` (the default), **When** the service starts and a cook runs a
   search, a nutrition lookup, a substitution, and a meal-plan request, **Then** all four behaviors
   complete end-to-end exactly as they did before this feature.
2. **Given** the provider setting is `openai`, **When** the service is restarted with no source change,
   **Then** the same four behaviors complete end-to-end with the same observable contracts (same tool
   calls, same validated inputs, same bounded loop, same wall filtering).
3. **Given** the provider setting holds an unknown or missing value, **When** the service starts,
   **Then** startup fails fast with a clear error naming the offending setting rather than starting in a
   broken or silently-degraded state.
4. **Given** either provider is selected, **When** the service resolves its credentials at startup,
   **Then** the provider's API key is read from the secret store (Vault) and never from environment,
   code, image, or `.env.example`.
5. **Given** a cook attempts an allergen-override or prompt-injection under either provider, **When** the
   turn is processed, **Then** it is refused identically — the wall and guardrails behave the same
   regardless of which provider is active.

---

### User Story 2 - Inspect and repair data visually with pgAdmin (Priority: P2)

The operator opens a pgAdmin web UI that is already connected to the local Postgres database and browses
the recipe corpus, favorites, and seen-history tables — running a query to confirm, for example, a
recipe's allergen tags — without writing connection strings or memorizing SQL clients. The tool is a
local development convenience and is never part of the deployed stack.

**Why this priority**: It is a self-contained operability win that does not touch the generation path or
any safety behavior, so it carries lower risk and can ship independently of User Story 1. It directly
serves the demo-day goal of letting a junior operator see and fix data quickly.

**Independent Test**: Bring up the local stack, open pgAdmin in a browser, confirm it is already
connected to the Postgres server on first load (no manual connection setup), and run a query against the
recipes table to read allergen tags. Separately, verify the deployed service set does **not** include
pgAdmin.

**Acceptance Scenarios**:

1. **Given** a fresh local stack startup, **When** the operator opens pgAdmin, **Then** the Postgres
   server connection is already pre-provisioned and usable without manual configuration.
2. **Given** pgAdmin is open and connected, **When** the operator queries the corpus, favorites, or
   seen-history tables, **Then** the current data is returned and editable for inspection/repair.
3. **Given** the production deploy is built and launched, **When** its running services are enumerated,
   **Then** pgAdmin is absent from the deployed stack.
4. **Given** the repository and its example configuration, **When** they are inspected, **Then** no
   pgAdmin password (and no other secret) appears unredacted in any log, trace, image, or example config.

---

### Edge Cases

- **Provider configured but its key is absent from Vault**: startup must fail fast with a clear error
  identifying the missing secret, never start with a non-functional generation path.
- **A tool call returned by one provider has a slightly different wire shape than the other**: the seam
  normalizes both into the single internal tool-call shape the agent expects, so the bounded loop and
  constraint guard see identical structure regardless of provider.
- **Provider outage at runtime**: the existing per-turn error handling (graceful degradation / honest
  failure) applies unchanged; selecting the other provider and restarting is the recovery path. (Automatic
  runtime failover between providers is out of scope — see Assumptions.)
- **Operator edits data in pgAdmin that violates a constraint** (e.g., removes an allergen tag): the
  deterministic constraint guard still enforces the wall at query time on every output path, so a manual
  data edit cannot cause an unsafe recipe to be surfaced.
- **pgAdmin reachable from outside the local machine**: pgAdmin is bound to local use only and is excluded
  from the deployed environment, so it presents no production exposure.

## Requirements *(mandatory)*

### Functional Requirements

#### LLM provider seam

- **FR-001**: The system MUST route all LLM text generation and tool-calling through a single internal
  seam so that no business-logic call site (router fallback, RAG explainer, bounded agent) is aware of
  which provider is active.
- **FR-002**: The system MUST select the active generation provider from one configuration value, with
  **Groq as the default**, and OpenAI as the alternative.
- **FR-003**: Swapping the provider MUST require no source-code change at any call site — only the
  configuration value and a restart.
- **FR-004**: Both providers MUST expose the identical generation contract — accepting messages, optional
  tool specifications, an optional token cap, and an optional model override — and MUST return tool calls
  in a single normalized shape consumed by the bounded agent.
- **FR-005**: The system MUST validate the provider setting at startup and **fail fast with a clear,
  specific error** when the value is unknown or missing, rather than starting in a degraded state.
- **FR-006**: The system MUST read each provider's API key from the secret store (Vault) at startup,
  exactly as the existing Groq key is resolved; provider keys MUST NOT appear in environment files, code,
  images, or example configuration.
- **FR-007**: The tool-calling contract — tool specifications, schema-validated tool inputs, the bounded
  iteration/token loop, and the constraint-guard wall — MUST be identical across providers; swapping the
  provider MUST NOT change any behavior contract or safety guarantee.
- **FR-008**: The safety wall and the input/output guardrails MUST remain deterministic and
  provider-independent; their behavior MUST be byte-for-byte unaffected by the active provider.
- **FR-009**: Version-controlled prompts MUST remain unchanged by the swap; the provider change MUST NOT
  require editing any prompt.
- **FR-009a**: Observability MUST achieve parity across providers: each turn served by **either** provider
  MUST emit the same Phoenix spans (router → retrieval → tool calls) and the same per-turn token/cost
  attribution, with redaction applied before any span is emitted. Selecting OpenAI MUST NOT regress
  tracing or cost attribution relative to Groq.
- **FR-010**: The system MUST allow the model name used by each provider (and the agent's model, if
  distinct) to be configured as non-secret settings, documented alongside the existing Groq model knobs.
- **FR-011**: An automated contract test MUST assert that both provider adapters satisfy the same
  interface and return tool calls in the same normalized shape, without making real network calls.

#### pgAdmin operability

- **FR-012**: The local development stack MUST include a pgAdmin web UI connected to the existing local
  Postgres database. The canonical one-command bring-up (`make up`) MUST start pgAdmin alongside the
  stack; pgAdmin lives under a local-only compose profile, so a bare `docker compose up` without that
  profile intentionally omits it (reinforcing the local/dev-only and never-deployed posture).
- **FR-013**: The pgAdmin Postgres server connection MUST be pre-provisioned so it is usable on first
  boot without the operator manually entering connection details.
- **FR-014**: pgAdmin MUST allow the operator to browse and edit the corpus, favorites, and seen-history
  data for inspection and repair.
- **FR-015**: pgAdmin MUST be excluded from the deployed (Railway) service set; it is a local/dev
  convenience only.
- **FR-016**: pgAdmin's local login credentials MUST be treated as a local-only convenience (not a Vault
  secret, not deployed) and MUST NOT appear unredacted in any log, trace, or image.

#### Cross-cutting constraints

- **FR-017**: The feature MUST NOT add `torch` or any heavy ML runtime to any image, and MUST NOT add a
  new runtime Python dependency for OpenAI chat — it MUST reuse the already-vendored `openai` SDK.
- **FR-018**: The deterministic constraint guard MUST continue to enforce the wall on every output path
  regardless of provider selection or any manual data edit performed via pgAdmin.

### Key Entities *(include if feature involves data)*

- **LLM Provider Selection**: The single configuration value that names the active generation provider
  (`groq` or `openai`) plus the per-provider model names; all non-secret.
- **Provider Credential**: The per-provider API key, stored in and resolved from Vault, never persisted
  in code/env/image/example config.
- **Generation Contract**: The provider-agnostic interface — messages in, optional tools/token-cap/model,
  text-or-tool-calls out in one normalized shape — that the seam guarantees both adapters satisfy.
- **pgAdmin Service**: The local-only database UI with a pre-provisioned connection to the local Postgres,
  excluded from production.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The full demo (search, nutrition, substitution, a multi-step meal plan) completes
  end-to-end under **both** provider settings, with **zero source-code changes** between the two runs.
- **SC-002**: Switching providers is achieved by changing exactly **one** configuration value plus a
  restart — no other edit is required anywhere in the codebase.
- **SC-003**: With an unknown or missing provider value, the service **fails to start** and emits a
  single clear error identifying the offending setting (no silent degradation).
- **SC-004**: A contract test passes for **both** adapters, proving identical interface and identical
  normalized tool-call shape, with **no real network calls**.
- **SC-005**: 100% of red-team probes (allergen-override + injection/jailbreak) are refused under **both**
  providers — the existing safety gate stays green with the provider swapped.
- **SC-005a**: Under **both** providers, every turn produces a Phoenix trace with per-turn token/cost
  attribution (redacted before emission); no span or cost field present for Groq is missing for OpenAI.
- **SC-006**: No secret (either provider's API key, the pgAdmin password) appears unredacted in any log,
  trace, image, or example configuration — verified by the existing redaction check plus inspection.
- **SC-007**: From a fresh local startup (`make up`), the operator opens pgAdmin and runs a query
  confirming a recipe's allergen tags in **under 2 minutes**, without manually configuring a database
  connection.
- **SC-008**: The deployed (Railway) service set contains **no** pgAdmin service.
- **SC-009**: No new runtime Python dependency is added for OpenAI chat, and no image gains `torch`;
  image sizes stay within the existing lean budget.

## Assumptions

- **Default provider is Groq.** Existing behavior is preserved unless the operator explicitly selects
  OpenAI; current deployments keep working with no configuration change.
- **The `openai` SDK is already vendored** in the backend dependency group (for embeddings), so the
  OpenAI chat adapter adds no new runtime dependency.
- **Provider switching is a startup-time, config-driven choice**, not a per-request or automatic runtime
  failover mechanism. Live automatic failover between providers is out of scope for this feature.
- **pgAdmin is local/dev only.** It is bound to local use, pre-connected to the local Postgres, and never
  added to the Railway deploy. It sits under a local compose profile that `make up` (the canonical
  bring-up) activates; a bare `docker compose up` without the profile omits it. Its credentials are a
  local convenience, not a managed secret.
- **The existing Vault secrets adapter is reused** to resolve the OpenAI key the same way the Groq key is
  resolved today.
- **The bounded agent, router, RAG explainer, constraint guard, guardrails, and prompts already exist**
  (from the prior AI-integration phase) and are the components this seam abstracts; this feature does not
  add new cook-facing business logic.
- **Specific model identifiers** for each provider are configurable non-secret settings, pinned to
  committed defaults for reproducibility (P5): `openai_model` defaults to `gpt-4o-mini` (fast/workflow)
  and `openai_agent_model` defaults to `gpt-4o` (the stronger agent model), documented next to the
  existing `groq_model`/`groq_agent_model` knobs and overridable via `OPENAI_MODEL`/`OPENAI_AGENT_MODEL`.
