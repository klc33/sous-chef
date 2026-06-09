# Setup Requirements — what YOU need to provide

SousChef keeps **all secrets in HashiCorp Vault**, never in the repo, `.env`, or a Docker image
(golden rule #4). This guide lists exactly what you must supply, where it goes, and step-by-step how
to get each value. You only need to do this once per machine.

> TL;DR: get two API keys, export them in your shell, run `make seed`. Nothing else is required to
> boot the stack.

---

## 1. What you must fill in

| Secret | Used for | Required? | Where to get it (see below) |
|---|---|---|---|
| `GROQ_API_KEY` | LLM chat + the bounded agent (Groq is chat-only) | **Yes**, for any chat/agent turn | [Step A](#step-a--get-a-groq-api-key) |
| `EMBEDDINGS_API_KEY` | Embedding recipes + queries (OpenAI-compatible provider) | **Yes**, for search / `make ingest` | [Step B](#step-b--get-an-embeddings-api-key-openai) |

That's it — there are **no other secrets** to provide. Everything else has a working default.

The dev stack will *boot* without real keys (it seeds harmless placeholders), but the first real
hosted call (a chat turn, or `make ingest` embedding the corpus) will fail at the provider until you
seed real keys. That failure is intentional — it's the signal to come back here.

---

## 2. Where the keys go (and where they DON'T)

- ✅ **Into Vault**, via `make seed`. The seed script reads the keys from your shell environment and
  writes them to Vault at `secret/sous-chef`. The app loads them from Vault at startup.
- ❌ **Never** put keys in `.env`, `.env.example`, source code, or a Dockerfile. `.env` holds only
  non-secret locations (Vault address/token, Postgres/Redis URLs).

The app looks up the keys by these exact names: `GROQ_API_KEY`, `EMBEDDINGS_API_KEY`.

---

## 3. Step-by-step

### Step A — Get a Groq API key

1. Go to **https://console.groq.com** and sign in (Google/GitHub or email — it's free to start).
2. In the left sidebar, open **API Keys**.
3. Click **Create API Key**, give it a name (e.g. `souschef-local`), and **Create**.
4. **Copy the key immediately** — Groq shows it only once. It starts with `gsk_...`.
5. Keep it somewhere safe for the next step.

### Step B — Get an embeddings API key (OpenAI)

The default embeddings provider is OpenAI (`text-embedding-3-small`, 1536 dims). Any
OpenAI-compatible provider works — see [Step C](#step-c-optional--use-a-different-embeddings-provider).

1. Go to **https://platform.openai.com** and sign in / create an account.
2. Open **https://platform.openai.com/api-keys** (top-right profile menu → *API keys*).
3. Click **Create new secret key**, name it (e.g. `souschef-embeddings`), and **Create**.
4. **Copy the key immediately** — it's shown only once. It starts with `sk-...`.
5. Embeddings require **billing to be enabled**: go to **Billing → Payment methods**, add a card,
   and add a small credit (a few dollars covers the whole corpus — `text-embedding-3-small` is very
   cheap). Without billing the key returns a quota error.

### Step C (optional) — Use a different embeddings provider

If you'd rather not use OpenAI, point the app at any OpenAI-compatible embeddings endpoint by
overriding two **non-secret** values in your `.env` (NOT the key — the key still goes through Vault):

```bash
EMBEDDINGS_BASE_URL=https://your-provider.example/v1
EMBEDDINGS_MODEL=their-embedding-model
```

> ⚠️ The embedding **dimension is pinned to 1536** by the database migration. If your model emits a
> different dimension, the app fails fast at startup. Changing it requires a new Alembic migration —
> stick with a 1536-dim model unless you intend to do that.

### Step D — Seed the keys into Vault

With both keys copied, run this **once** (replace the placeholders with your real keys):

**macOS / Linux / Git Bash:**
```bash
export GROQ_API_KEY=gsk_your_real_groq_key
export EMBEDDINGS_API_KEY=sk-your_real_openai_key
make seed
```

**Windows PowerShell:**
```powershell
$env:GROQ_API_KEY = "gsk_your_real_groq_key"
$env:EMBEDDINGS_API_KEY = "sk-your_real_openai_key"
make seed   # requires the dev stack to be up (make up) so the Vault container is running
```

`make seed` runs inside the running `backend` container and writes the keys to Vault. It's
idempotent — safe to re-run any time you rotate a key. (If you skip the `export`/`$env:` lines,
`make seed` writes harmless placeholders instead, and real hosted calls will fail.)

### Step E — Verify

1. Bring the stack up: `make up` (first run also copies `.env` from `.env.example`).
2. Seed real keys: Step D.
3. The keys are now in Vault. A chat turn or `make ingest` will use them. If a call fails with an
   auth/quota error, re-check the key value and that billing is enabled (embeddings), then re-seed.

---

## 4. Quick reference — non-secret config you may override

These live in [`app/config.py`](app/config.py) with working defaults; override in `.env` only if
needed. **None of these are secrets.**

| Env var | Default | Meaning |
|---|---|---|
| `EMBEDDINGS_BASE_URL` | `https://api.openai.com/v1` | Embeddings provider endpoint |
| `EMBEDDINGS_MODEL` | `text-embedding-3-small` | Embedding model (must be 1536-dim) |
| `GROQ_MODEL` | `llama-3.1-8b-instant` | Groq model for the workflow path (search/nutrition/chitchat) — fast |
| `GROQ_AGENT_MODEL` | `llama-3.3-70b-versatile` | Groq model for the bounded agent (meal-plan) — reliable multi-tool |
| `ROUTER_CONFIDENCE_THRESHOLD` | `0.55` | Below this, a turn escalates to the agent |
| `RETRIEVAL_CANDIDATE_POOL` | `20` | Vector-search over-fetch before the allergen wall trims to 3 |
| `AGENT_MAX_ITERATIONS` | `5` | Bounded-agent iteration cap |
| `AGENT_TOKEN_BUDGET` | `8000` | Bounded-agent token cap |

Secrets (`GROQ_API_KEY`, `EMBEDDINGS_API_KEY`) are **not** in this table on purpose — they go through
Vault via `make seed`, never `.env`.
