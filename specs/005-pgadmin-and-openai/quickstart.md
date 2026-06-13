# Quickstart / Validation: Operability & Model Flexibility

Runnable scenarios that prove the feature end-to-end. Details live in [plan.md](plan.md),
[research.md](research.md), [data-model.md](data-model.md), and [contracts/llm_client.md](contracts/llm_client.md).

## Prerequisites

- Docker + docker-compose; `uv` for tests.
- Real provider keys exported before seeding for live hosted calls (placeholders boot fine but fail at the
  provider on a real call):
  ```bash
  export GROQ_API_KEY=...           # default provider
  export OPENAI_API_KEY=...         # required only to exercise the OpenAI path
  export EMBEDDINGS_API_KEY=...     # embeddings (unchanged, separate provider)
  ```

## Scenario A — Default provider (Groq) still works (US1, SC-001)

```bash
make up            # brings up backend + datastores + UIs + pgAdmin (local profile)
```
Then run the demo via the widget (or curl `/chat`): a search, a nutrition lookup, a substitution, and a
meal plan. **Expected**: all four complete end-to-end exactly as before (LLM_PROVIDER unset → defaults to
`groq`). No source change was made to switch.

## Scenario B — Swap to OpenAI with one setting (US1, SC-002)

```bash
echo "LLM_PROVIDER=openai" >> .env     # the ONE change; optionally set OPENAI_MODEL / OPENAI_AGENT_MODEL
export OPENAI_API_KEY=...              # then re-seed Vault so the key is present
make seed
make up                                 # restart
```
Run the same four behaviors. **Expected**: identical contracts — same tool calls, same validated inputs,
same bounded loop, same wall filtering — with **no code change** between the runs. Flip back by removing
the line and restarting; the demo runs again (SC-001/SC-002).

## Scenario C — Fail fast on a bad provider value (US1, SC-003)

```bash
echo "LLM_PROVIDER=bogus" >> .env && make up
```
**Expected**: startup fails with a single clear settings error naming `llm_provider`/`LLM_PROVIDER`; the
service does not start in a degraded state. (Remove the line to recover.)

## Scenario D — Secrets never leak (SC-006)

- Grep logs/traces for the OpenAI key and pgAdmin password → **no unredacted match**.
- `.env.example` contains **no** real secret; `OPENAI_API_KEY` appears only as a note that it lives in
  Vault. pgAdmin credentials in `.env.example` are obvious local placeholders.

## Scenario E — Observability parity (FR-009a, SC-005a)

With Phoenix up, run a `/chat` turn under **each** provider and open the trace. **Expected**: each turn's
span carries `llm.provider`, `llm.model`, and `llm.total_tokens`; no attribute present for Groq is missing
for OpenAI; values are redaction-clean.

## Scenario F — pgAdmin inspect/repair (US2, SC-007, SC-008)

1. After `make up`, open `http://localhost:5050` and log in with the local pgAdmin credentials from `.env`.
2. **Expected**: the `souschef` Postgres server is **already** present (pre-provisioned via
   `servers.json`) — no manual connection setup. Browse `recipes`/`ingredients` and run a query confirming
   a recipe's allergen tags, in **< 2 minutes** (SC-007). `make pgadmin` prints/opens the URL.
3. Confirm **exclusion from deploy** (SC-008): `railway.toml` defines only the backend; `pgadmin` is under
   the `local` compose profile and is not a Railway service.

## Automated gates (`make lint && make test && make evals`)

- **Contract test** (`tests/contract/test_llm_client.py`): both adapters satisfy `LLMClient` and return the
  same normalized tool-call shape, **no network** (SC-004).
- **Re-pointed tests**: `test_rag`, `test_chat_flow`, `test_wall_regression` monkeypatch `llm.chat` (was
  `llm_groq.chat`) and pass unchanged — proving the import-only migration.
- **Red-team gate**: 100% refused; must stay green under both providers (SC-005). Optionally parametrize the
  agent tool-selection eval by provider (report-only).
- **Redaction gate**: a pasted fake secret never appears unredacted (SC-006).
