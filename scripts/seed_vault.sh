#!/usr/bin/env sh
# Seed the dev Vault with the app's secrets at the KV v2 path app/infra/vault.py reads
# (mount "secret", path "sous-chef"). Idempotent: a KV v2 write overwrites the path, so this
# is safe to run on every boot and to re-run manually via `make seed`.
#
# Secrets here are throwaway DEV PLACEHOLDERS only — real provider keys are added in their
# own phases and never committed (golden rule #4: secrets live in Vault, not the repo).
set -eu

VAULT_ADDR="${VAULT_ADDR:-http://vault:8200}"
VAULT_TOKEN="${VAULT_TOKEN:-root}"

# KV v2 data path is /v1/<mount>/data/<path>. Body wraps values under "data".
curl -sf -X POST \
  -H "X-Vault-Token: ${VAULT_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"data":{"app_secret":"dev-placeholder-not-a-real-secret"}}' \
  "${VAULT_ADDR}/v1/secret/data/sous-chef" >/dev/null

echo "seed_vault: wrote secret/sous-chef to ${VAULT_ADDR}"
