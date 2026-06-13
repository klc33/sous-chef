# Switching the chat/agent LLM provider (Groq ⇄ OpenAI)

The chat/agent **generation** provider is selectable with one setting — no source change. Embeddings are
**not** affected (they keep their own provider/key). See [docs/DECISIONS.md](docs/DECISIONS.md) **D9** for the
seam design.

## What decides the active provider

1. **`LLM_PROVIDER`** in `.env` — `groq` (default) or `openai`.
2. The **matching key in Vault** — `GROQ_API_KEY` / `OPENAI_API_KEY`.
3. A **backend restart** — the provider is read once at startup.

`GROQ_API_KEY` is already durable (it's in `.env`, so the boot-seed re-writes it on every start). The catch is
`OPENAI_API_KEY`: if it's **not** in `.env`, every restart's boot-seed resets it to a placeholder and you'd
have to re-patch Vault by hand. Do the one-time setup below and swapping becomes trivial.

---

## One-time setup — make the OpenAI key durable

Add the key to `.env` (same value as your `EMBEDDINGS_API_KEY` — that's already an OpenAI key). Then the
boot-seed writes the real key on every start, exactly like Groq:

```powershell
Add-Content -Path .env -Encoding ascii -Value "OPENAI_API_KEY=<paste the same sk-proj... value as EMBEDDINGS_API_KEY>"
```

After this, no manual Vault patching is ever needed again.

> Note: this puts a secret in `.env` (technically against the project's "secrets only in Vault" rule), but
> `.env` already holds the Groq/embeddings keys the same way, so it's consistent with the local setup. `.env`
> is gitignored.

---

## Switch to OpenAI

```powershell
# 1. set the provider line in .env
(Get-Content .env) -replace '^LLM_PROVIDER=.*','LLM_PROVIDER=openai' | Set-Content -Encoding ascii .env

# 2. restart the backend so it re-reads .env (recreates the container)
docker compose up -d backend
```

Optional model knobs (defaults are fine): `OPENAI_MODEL` (workflow) and `OPENAI_AGENT_MODEL` (agent) — both
default to `gpt-4o-mini`. Raise `OPENAI_AGENT_MODEL` (e.g. `gpt-4o`) only for a stronger model on the agent path.

## Switch to Groq

```powershell
(Get-Content .env) -replace '^LLM_PROVIDER=.*','LLM_PROVIDER=groq' | Set-Content -Encoding ascii .env
docker compose up -d backend
```

`groq` is the default, so deleting the `LLM_PROVIDER` line entirely is equivalent.

---

## Verify which provider is live (no paid API call)

```powershell
docker compose exec backend python -c "from app.infra.llm.factory import get_client; from app.config import get_settings; s=get_settings(); print('provider=',s.llm_provider,'| client=',type(get_client()).__name__,'| workflow=',(s.openai_model if s.llm_provider=='openai' else s.groq_model),'| agent=',s.agent_model)"
```

Expect e.g. `provider= openai | client= OpenAIClient | workflow= gpt-4o-mini | agent= gpt-4o-mini`.

Then a real end-to-end check:

```powershell
$h=@{"X-Profile-ID"="swaptest";"Content-Type"="application/json"}
Invoke-RestMethod http://localhost:8000/chat -Method Post -Headers $h -Body (@{message="dinner ideas with chicken"}|ConvertTo-Json) | Select-Object intent,reply
```

---

## Two gotchas worth knowing

- **Rebuild only when code changes.** A plain provider swap needs just `docker compose up -d backend`
  (recreate). Use `docker compose up -d --build backend` **only** when the `app/` code changed (e.g. the first
  time the image predated the seam). Normal swapping does **not** rebuild.
- **Fail-fast on a typo.** A bad value (e.g. `LLM_PROVIDER=opena`) makes the backend refuse to start with a
  clear `llm_provider` settings error — check `docker compose logs backend` if it doesn't come up healthy.
