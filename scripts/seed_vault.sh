#!/usr/bin/env sh
# Seed the dev Vault with the app's secrets at the KV v2 path app/infra/vault.py reads
# (mount "secret", path "sous-chef"). Idempotent: a KV v2 write overwrites the path, so this
# is safe to run on every boot and to re-run manually via `make seed`.
#
# Provider keys are read from the OPERATOR'S ENVIRONMENT when present and fall back to throwaway
# DEV PLACEHOLDERS otherwise — so real keys reach Vault without ever touching the repo, .env, or an
# image (golden rule #4: secrets live in Vault). To seed real keys for local hosted calls:
#   export GROQ_API_KEY=... EMBEDDINGS_API_KEY=... OPENAI_API_KEY=...   # then: make seed
# EMBEDDINGS_API_KEY is the key for the OpenAI-compatible embeddings provider (text-embedding-3-small).
# OPENAI_API_KEY is the chat key used ONLY when LLM_PROVIDER=openai (the provider-agnostic LLM seam, 005);
# it follows the exact same Vault pattern as GROQ_API_KEY and is dormant while the default (groq) is active.
set -eu

VAULT_ADDR="${VAULT_ADDR:-http://vault:8200}"
VAULT_TOKEN="${VAULT_TOKEN:-root}"

# Default to obvious non-secret placeholders so a fresh boot succeeds; a real hosted call made with a
# placeholder fails fast at the provider, which is the desired signal to re-run `make seed` with keys.
GROQ_API_KEY="${GROQ_API_KEY:-dev-placeholder-groq-key}"
EMBEDDINGS_API_KEY="${EMBEDDINGS_API_KEY:-dev-placeholder-embeddings-key}"
OPENAI_API_KEY="${OPENAI_API_KEY:-dev-placeholder-openai-key}"
# LangSmith Cloud OTLP key — used ONLY when TRACING_PROVIDER=langsmith (else dormant, same pattern as
# OPENAI_API_KEY). Local dev defaults to self-hosted Phoenix, so the placeholder is fine out of the box.
LANGSMITH_API_KEY="${LANGSMITH_API_KEY:-dev-placeholder-langsmith-key}"

# Operator-dashboard secrets (004-evals-and-uis). Same env-forward-or-placeholder pattern as the
# provider keys above: real values are exported in the operator's shell before `make seed`; a fresh
# boot falls back to working DEV placeholders so the dashboard logs in out of the box. Key names match
# app.config.VAULT_KEY_* and what admin_deps / dashboard auth look up at runtime.
#   OPERATOR_PASSWORD_HASH — bcrypt hash streamlit-authenticator checks. Dev default is the hash of
#     the password "souschef-dev" (the `\$` keep the literal '$' segments out of shell expansion).
#   DASHBOARD_COOKIE_KEY   — signs the login cookie so a refresh keeps the operator logged in.
#   ADMIN_API_TOKEN        — shared bearer token the dashboard sends; the backend fails fast without it.
OPERATOR_PASSWORD_HASH="${OPERATOR_PASSWORD_HASH:-\$2b\$12\$krAZGw9bKfb8eWOFFCi8iuajtqTRzI9jrJ.PfEgGUiW/cTmy37eVe}"
DASHBOARD_COOKIE_KEY="${DASHBOARD_COOKIE_KEY:-dev-placeholder-dashboard-cookie-key}"
ADMIN_API_TOKEN="${ADMIN_API_TOKEN:-dev-placeholder-admin-api-token}"

# KV v2 data path is /v1/<mount>/data/<path>. Body wraps values under "data". The key names here are
# exactly what VaultAdapter.get(...) looks up at runtime (GROQ_API_KEY, EMBEDDINGS_API_KEY, OPENAI_API_KEY,
# and the operator-auth keys OPERATOR_PASSWORD_HASH, DASHBOARD_COOKIE_KEY, ADMIN_API_TOKEN).
curl -sf -X POST \
  -H "X-Vault-Token: ${VAULT_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"data\":{\"app_secret\":\"dev-placeholder-not-a-real-secret\",\"GROQ_API_KEY\":\"${GROQ_API_KEY}\",\"EMBEDDINGS_API_KEY\":\"${EMBEDDINGS_API_KEY}\",\"OPENAI_API_KEY\":\"${OPENAI_API_KEY}\",\"LANGSMITH_API_KEY\":\"${LANGSMITH_API_KEY}\",\"OPERATOR_PASSWORD_HASH\":\"${OPERATOR_PASSWORD_HASH}\",\"DASHBOARD_COOKIE_KEY\":\"${DASHBOARD_COOKIE_KEY}\",\"ADMIN_API_TOKEN\":\"${ADMIN_API_TOKEN}\"}}" \
  "${VAULT_ADDR}/v1/secret/data/sous-chef" >/dev/null

echo "seed_vault: wrote secret/sous-chef (app_secret, GROQ_API_KEY, EMBEDDINGS_API_KEY, OPENAI_API_KEY, LANGSMITH_API_KEY, OPERATOR_PASSWORD_HASH, DASHBOARD_COOKIE_KEY, ADMIN_API_TOKEN) to ${VAULT_ADDR}"
