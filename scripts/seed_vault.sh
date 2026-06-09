#!/usr/bin/env sh
# Seed the dev Vault with the app's secrets at the KV v2 path app/infra/vault.py reads
# (mount "secret", path "sous-chef"). Idempotent: a KV v2 write overwrites the path, so this
# is safe to run on every boot and to re-run manually via `make seed`.
#
# Provider keys are read from the OPERATOR'S ENVIRONMENT when present and fall back to throwaway
# DEV PLACEHOLDERS otherwise — so real keys reach Vault without ever touching the repo, .env, or an
# image (golden rule #4: secrets live in Vault). To seed real keys for local hosted calls:
#   export GROQ_API_KEY=... EMBEDDINGS_API_KEY=...   # then: make seed
# EMBEDDINGS_API_KEY is the key for the OpenAI-compatible embeddings provider (text-embedding-3-small).
set -eu

VAULT_ADDR="${VAULT_ADDR:-http://vault:8200}"
VAULT_TOKEN="${VAULT_TOKEN:-root}"

# Default to obvious non-secret placeholders so a fresh boot succeeds; a real hosted call made with a
# placeholder fails fast at the provider, which is the desired signal to re-run `make seed` with keys.
GROQ_API_KEY="${GROQ_API_KEY:-dev-placeholder-groq-key}"
EMBEDDINGS_API_KEY="${EMBEDDINGS_API_KEY:-dev-placeholder-embeddings-key}"

# KV v2 data path is /v1/<mount>/data/<path>. Body wraps values under "data". The key names here are
# exactly what VaultAdapter.get(...) looks up at runtime (GROQ_API_KEY, EMBEDDINGS_API_KEY).
curl -sf -X POST \
  -H "X-Vault-Token: ${VAULT_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"data\":{\"app_secret\":\"dev-placeholder-not-a-real-secret\",\"GROQ_API_KEY\":\"${GROQ_API_KEY}\",\"EMBEDDINGS_API_KEY\":\"${EMBEDDINGS_API_KEY}\"}}" \
  "${VAULT_ADDR}/v1/secret/data/sous-chef" >/dev/null

echo "seed_vault: wrote secret/sous-chef (app_secret, GROQ_API_KEY, EMBEDDINGS_API_KEY) to ${VAULT_ADDR}"
