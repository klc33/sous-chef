# Phase 1 Data Model: Operability & Model Flexibility

This feature introduces **no database schema change** (no migration). pgAdmin reads/writes the *existing*
tables; the LLM seam adds configuration, a secret, and an in-process contract. The "entities" below are
configuration values, a secret, the generation contract, and the local service — not persisted rows.

## Configuration entities (non-secret — `app/config.py`, surfaced in `.env.example`)

| Setting | Env var | Type / values | Default | Notes |
|---|---|---|---|---|
| `llm_provider` | `LLM_PROVIDER` | `Literal["groq","openai"]` | `groq` | Selects the active chat/agent generation provider. Unknown value → fail fast at settings load (FR-005, SC-003). |
| `openai_model` | `OPENAI_MODEL` | `str` | `gpt-4o-mini` | Fast/workflow model for the RAG explainer when provider is OpenAI. Mirrors `groq_model`. |
| `openai_agent_model` | `OPENAI_AGENT_MODEL` | `str` | `gpt-4o-mini` | Agent model the bounded loop uses (resolved per-provider via `Settings.agent_model`). Defaults to `gpt-4o-mini` (cheap, tool-calling-capable); raise to `gpt-4o` for a stronger agent. Mirrors `groq_agent_model`. |

Existing related settings (unchanged): `groq_model`, `groq_agent_model`, `embeddings_base_url`,
`embeddings_model`, `embeddings_dim`. `LLM_PROVIDER` does **not** affect embeddings (Decision 7).

**Validation rules**:
- `llm_provider` must be one of `groq`/`openai` (pydantic `Literal`); any other value raises at load.
- Default selection (`groq`) preserves current behavior with zero config change.
- Model names are committed non-secret config (Principle V); documented next to the `GROQ_MODEL` knobs.

## Secret entity (Vault only)

| Secret key | Where written | Where read | Constraint |
|---|---|---|---|
| `OPENAI_API_KEY` | `scripts/seed_vault.sh` (env-forward-or-placeholder, like `GROQ_API_KEY`) | `app/infra/llm/openai.py` via `VaultAdapter.get("OPENAI_API_KEY")` | Never in env/code/image/`.env.example` (FR-006, SC-006). `.env.example` only *notes* it is a Vault secret. |

Existing secrets unchanged: `GROQ_API_KEY`, `EMBEDDINGS_API_KEY`, `ADMIN_API_TOKEN`,
`OPERATOR_PASSWORD_HASH`, `DASHBOARD_COOKIE_KEY`. The OpenAI key follows the **exact** Groq key pattern.

**Lifecycle**: written at seed/boot → loaded once by `VaultAdapter.load_secrets()` → served in-process via
`get()`. A missing key for the *selected* provider raises `StartupConfigError` on first generation call;
the unselected provider's key may be a placeholder without affecting the active path.

## Generation contract entity (in-process — the seam)

**`LLMClient` Protocol** (`app/infra/llm/base.py`): one method —

```
chat(messages: list[dict], *, tools: list[dict] | None = None,
     max_tokens: int | None = None, model: str | None = None) -> Any
```

Returns an **OpenAI-style response object** exposing (the only fields callers read):

| Path | Meaning | Read by |
|---|---|---|
| `.choices[0].message.content` | assistant text (may be empty when tools are called) | `rag.py`, `agent/loop.py` |
| `.choices[0].message.tool_calls` | list of tool calls or `None` | `agent/loop.py` |
| `.choices[0].message.tool_calls[*].id` | tool-call id (links result back) | `agent/loop.py` |
| `.choices[0].message.tool_calls[*].function.name` | tool name | `agent/loop.py` |
| `.choices[0].message.tool_calls[*].function.arguments` | JSON string of args | `agent/loop.py` |
| `.usage.total_tokens` | cumulative tokens for budget + span attribution | `agent/loop.py`, facade span tagging |

**Invariants** (asserted by the contract test):
- Both `groq` and `openai` adapters satisfy the Protocol (structural check).
- Given a mocked transport returning a tool call, both adapters expose it at the **same** attribute paths
  above (same normalized shape) — FR-004, FR-011, SC-004.
- No real network call occurs in the contract test.

## Observability attributes (span, non-secret — Decision 5)

Emitted best-effort by the facade onto the active span after a successful call: `llm.provider`
(`groq`/`openai`), `llm.model` (the resolved model id), `llm.total_tokens` (int). Redacted-before-export
like all span attributes; identical across providers (FR-009a, SC-005a).

## Local service entity (compose — pgAdmin)

| Attribute | Value |
|---|---|
| Image | `dpage/pgadmin4` |
| Compose profile | `local` (activated by `make up`; absent from a bare `docker compose up`) |
| Port | `5050:80` (local) |
| Depends on | `postgres` (healthy) |
| Pre-provisioned connection | `docker/pgadmin/servers.json` → host `postgres`, port `5432`, db `souschef`, user `postgres` |
| Credentials | `PGADMIN_DEFAULT_EMAIL` / `PGADMIN_DEFAULT_PASSWORD` — **local-only** env in `.env.example`, NOT Vault, NOT deployed |
| Deploy posture | Excluded from Railway (railway.toml backend-only) **and** profile-gated (FR-015, SC-008) |
| Data accessed | existing `recipes`, `ingredients`, `favorites`, `seen_history`, etc. — read **and** write (inspect/repair) |
