# Implementation Plan: Operability & Model Flexibility — pgAdmin + a Provider-Agnostic LLM Seam

**Branch**: `005-pgadmin-and-openai` | **Date**: 2026-06-13 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/005-pgadmin-and-openai/spec.md`

## Summary

Two operator-facing additions that do **not** grow the production stack or touch safety:

1. **Provider-agnostic LLM seam** — replace the single `app/infra/llm_groq.py` module with an
   `app/infra/llm/` package: a `base.LLMClient` Protocol, a `groq.py` adapter (the current Groq code
   moved verbatim, 429-retry kept), a new `openai.py` adapter built on the **already-vendored** `openai`
   SDK, and a `get_client()` factory selecting by `settings.llm_provider`. The package `__init__.py` is a
   stable module-level facade exposing `chat(...)`, so the two call sites (`services/user/rag.py`,
   `app/agent/loop.py`) and the tests change only their import (`llm_groq` → `llm`). Both adapters return
   the same OpenAI-style response object (`.choices[0].message.{content,tool_calls}`, `.usage.total_tokens`)
   the agent already reads, so the tool-calling contract and token accounting are identical across
   providers. The facade attaches `llm.provider` / `llm.model` / `llm.total_tokens` to the active Phoenix
   span (best-effort) so per-turn token attribution is present and identical under both providers
   (FR-009a / SC-005a). Provider keys come from Vault exactly like `GROQ_API_KEY`.

2. **pgAdmin operability** — add a `pgadmin` (dpage/pgadmin4) service to `docker-compose.yml` under a
   `local` profile that `make up` activates, `depends_on` postgres, published on a local port, with a
   mounted `servers.json` pre-provisioning the Postgres connection so it works on first boot. Credentials
   are local-only convenience env (in `.env.example`, **not** Vault, **not** deployed). It is absent from
   the Railway deploy structurally (railway.toml deploys only the backend; compose services are not Railway
   services) and is further gated behind the `local` profile.

No new runtime dependency, no `torch`, prompts unchanged, the deterministic wall and guardrails untouched.

## Technical Context

**Language/Version**: Python 3.12 (backend), shell (seed script), YAML/JSON (compose, servers.json)

**Primary Dependencies**: FastAPI; `groq` SDK (existing); `openai>=2.41.0` SDK (already in the `backend`
optional group for embeddings — reused for chat); `hvac` (Vault); OpenTelemetry + Arize Phoenix
(existing); pydantic-settings. Container image: `dpage/pgadmin4` (local compose only, never in a backend
image or the Railway deploy).

**Storage**: Existing PostgreSQL + pgvector (no schema change). pgAdmin reads/writes the existing
`recipes`, `ingredients`, `favorites`, `seen_history`, etc. tables for inspection/repair.

**Testing**: pytest. New: a fake `LLMClient` shared fixture; a contract test asserting both real adapters
satisfy the Protocol and emit the same normalized tool-call shape with a mocked transport (no network);
existing `test_rag` / `test_chat_flow` / `test_wall_regression` re-pointed from `llm_groq` to the `llm`
facade.

**Target Platform**: Linux containers via docker-compose (local) and Railway (deploy; backend only).

**Project Type**: Web-service monolith (single FastAPI app) + sibling UIs; this feature touches `app/infra`
+ config + compose/ops, not cook-facing business logic.

**Performance Goals**: No new latency target. SC-007: operator opens pgAdmin and queries allergen tags in
**< 2 minutes** from a fresh local startup.

**Constraints**: No `torch`, no new runtime Python dependency for OpenAI chat (reuse vendored `openai`);
keep images lean; pgAdmin local-only; the wall + guardrails stay deterministic and provider-independent;
prompts in `prompts/` unchanged; secrets only in Vault (provider keys) — pgAdmin password is a local-only
convenience, never a Vault secret and never deployed.

**Scale/Scope**: Solo project; ≤2,000-recipe corpus; two call sites behind the seam; one new compose
service. The swap is a startup-time config choice, not runtime auto-failover (out of scope).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment |
|---|---|
| **I. Simplicity** | A small Protocol + two adapters + a factory; one new compose service. No vector store, no orchestration change. **PASS** |
| **II. Build only what's required** | Implements exactly the spec's two stories; auto-failover, A/B reporting, and OpenAI-embeddings are explicitly out of scope. **PASS** |
| **III. Separation of concerns** | The seam lives entirely in `app/infra/` (adapters for external services); `services/` and `agent/` depend on the facade, never on a provider SDK. `repo/` untouched. **PASS** |
| **IV. Testability** | Adapters are mockable (fake `LLMClient`); a no-network contract test proves interface parity; existing safety tests run unchanged under the facade. Red-team gate must stay 100% under both providers. **PASS** |
| **V. Reproducibility** | `make up` from a fresh clone brings up the stack incl. pgAdmin; no new pinned dep; provider models are committed non-secret config; default provider unchanged (groq). **PASS** |
| **VI. Security & Privacy** | OpenAI key resolved from Vault like the Groq key — never env/code/image/.env.example. Redaction-before-span preserved; the seam adds only non-secret attributes (provider/model/token count) to spans. pgAdmin password is local-only and never logged/traced/deployed. **PASS** |
| **VII. Maintainability** | One seam, one facade; clear file names (`base/groq/openai`); callers depend on one import. **PASS** |
| **VIII. Documentation-first** | This plan + research/data-model/contracts/quickstart precede code; DECISIONS/SECURITY/RUNBOOK updates are tasks. **PASS** |
| **IX. Spec-driven** | Generated via specify → clarify → plan; artifacts committed. **PASS** |
| **X. No unnecessary tech** | No new runtime dep (reuse vendored `openai`); no torch; pgAdmin is local-dev only, excluded from prod. **PASS** |

**Non-negotiable safety invariants**: the wall (`constraint_guard`) and guardrails remain deterministic
code and are byte-for-byte unaffected by provider selection; grounding is unchanged (no new generation
paths); a manual pgAdmin edit cannot bypass the guard because the guard runs at query time on every output
path. **No violations. Gate PASSES.** (Complexity Tracking left empty.)

## Project Structure

### Documentation (this feature)

```text
specs/005-pgadmin-and-openai/
├── plan.md              # This file
├── spec.md              # Feature spec (+ Clarifications)
├── research.md          # Phase 0 — decisions (provider seam shape, pgAdmin profile, observability)
├── data-model.md        # Phase 1 — config/secret/contract entities (no DB schema change)
├── quickstart.md        # Phase 1 — runnable validation (swap provider; open pgAdmin)
├── contracts/
│   └── llm_client.md     # Phase 1 — the LLMClient Protocol + normalized response/tool-call contract
└── checklists/
    └── requirements.md   # Spec quality checklist (already passing)
```

### Source Code (repository root)

```text
app/
├── config.py                      # + llm_provider (Literal[groq|openai], default groq),
│                                   #   openai_model, openai_agent_model; + VAULT_KEY_OPENAI_API_KEY
├── infra/
│   ├── llm/                        # NEW package (replaces llm_groq.py)
│   │   ├── __init__.py             #   stable facade: chat(...) + get_client() re-export; span tagging
│   │   ├── base.py                 #   LLMClient Protocol: chat(messages, *, tools, max_tokens, model)
│   │   ├── groq.py                 #   existing Groq adapter moved here (Vault GROQ_API_KEY, 429 retry)
│   │   ├── openai.py               #   NEW OpenAI adapter (vendored openai SDK, Vault OPENAI_API_KEY)
│   │   └── factory.py              #   get_client() selecting by settings.llm_provider (fail fast)
│   ├── llm_groq.py                 # REMOVED (moved into llm/groq.py)
│   ├── embeddings.py               # unchanged (separate embeddings provider; NOT governed by llm_provider)
│   └── vault.py                    # unchanged behavior; OPENAI_API_KEY now among loadable secrets
├── services/user/rag.py           # import change only: llm_groq -> llm
└── agent/loop.py                  # import change only: llm_groq -> llm

scripts/seed_vault.sh              # + OPENAI_API_KEY (env-forward-or-placeholder, like GROQ_API_KEY)
docker-compose.yml                 # + pgadmin service (profile: local) + servers.json mount
docker/pgadmin/servers.json        # NEW pre-provisioned Postgres connection for pgAdmin
.env.example                       # + LLM_PROVIDER/OPENAI_MODEL/OPENAI_AGENT_MODEL notes; OPENAI_API_KEY
│                                   #   noted as a Vault secret; PGADMIN_DEFAULT_EMAIL/PASSWORD (local-only)
Makefile                           # `make up` activates the `local` profile; + `make pgadmin`
railway.toml                       # unchanged (already backend-only; pgAdmin not added)
docs/{DECISIONS,SECURITY,RUNBOOK}.md   # seam rationale; OpenAI-key-in-Vault + pgAdmin-local-only; how-to

tests/
├── conftest.py                    # + fake_llm_client fixture (shared)
├── contract/test_llm_client.py    # NEW: both adapters satisfy Protocol + same tool-call shape (no net)
├── unit/test_rag.py               # re-point monkeypatch llm_groq.chat -> llm.chat
└── integration/{test_chat_flow,test_wall_regression}.py  # re-point monkeypatch to llm.chat
```

**Structure Decision**: Monolith, unchanged. The entire change is contained to `app/infra/` (the external
-adapter layer, per Principle III), `app/config.py`, the two existing call sites (import-only), ops files
(compose/Makefile/seed/env), and tests/docs. No `api/`, `repo/`, `services` business logic, `agent` logic,
prompt, or DB-schema changes.

## Complexity Tracking

> No constitution violations — no entries required.
