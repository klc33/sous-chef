#!/usr/bin/env sh
# Seed Vault with the app's secrets at the KV v2 path app/infra/vault.py reads
# (mount "secret", path "sous-chef"). Idempotent: a KV v2 write overwrites the path, so this
# is safe to run on every local boot and to re-run manually via `make seed`.
#
# Two modes, auto-detected from VAULT_ADDR (override with ALLOW_DEV_PLACEHOLDERS=1 / SEED_FORCE_PROD=1):
#   • LOCAL dev Vault (vault:8200 / localhost / 127.0.0.1): provider keys are read from the OPERATOR'S
#     ENVIRONMENT when present and fall back to throwaway DEV PLACEHOLDERS otherwise — so a fresh `make
#     up` boots out of the box, and real keys reach Vault without ever touching the repo, .env, or an
#     image (golden rule #4: secrets live in Vault). To seed real keys for local hosted calls:
#       export GROQ_API_KEY=... EMBEDDINGS_API_KEY=... OPENAI_API_KEY=...   # then: make seed
#   • PROD / persistent server-mode Vault (any other VAULT_ADDR): the one-time operator seed required by
#     contracts/secrets-keyspace.md (R4). Dev placeholders are REFUSED here — every mandatory secret must
#     be exported in the operator's shell, or the script fails fast and writes nothing. This prevents a
#     forgotten export from silently shipping a throwaway "dev-placeholder-*" value into production.
#       VAULT_ADDR=<prod> VAULT_TOKEN=<prod-root> \
#         GROQ_API_KEY=... EMBEDDINGS_API_KEY=... OPERATOR_PASSWORD_HASH=... \
#         DASHBOARD_COOKIE_KEY=... ADMIN_API_TOKEN=... sh scripts/seed_vault.sh
#
# EMBEDDINGS_API_KEY is the key for the OpenAI-compatible embeddings provider (text-embedding-3-small).
# OPENAI_API_KEY is the chat key used ONLY when LLM_PROVIDER=openai (the provider-agnostic LLM seam, 005);
# LANGSMITH_API_KEY is used ONLY when TRACING_PROVIDER=langsmith — both are DORMANT under the defaults
# (groq + self-hosted Phoenix), so they are optional in prod and stay empty until their provider is flipped.
set -eu

VAULT_ADDR="${VAULT_ADDR:-http://vault:8200}"
VAULT_TOKEN="${VAULT_TOKEN:-root}"

# Decide whether DEV PLACEHOLDERS are allowed. Allowed only when seeding a LOCAL dev Vault; a real
# (prod) VAULT_ADDR forces real-key mode so a missing export fails fast instead of writing a placeholder.
# ALLOW_DEV_PLACEHOLDERS=1 forces local mode (e.g. a non-standard local/CI address); SEED_FORCE_PROD=1
# forces prod mode (e.g. testing the guard against a local address).
case "${VAULT_ADDR}" in
  *vault:8200*|*localhost*|*127.0.0.1*) IS_LOCAL=1 ;;
  *) IS_LOCAL=0 ;;
esac
[ "${ALLOW_DEV_PLACEHOLDERS:-0}" = "1" ] && IS_LOCAL=1
[ "${SEED_FORCE_PROD:-0}" = "1" ] && IS_LOCAL=0

if [ "${IS_LOCAL}" = "1" ]; then
  # ── Local dev: env-forward-or-placeholder so a fresh boot succeeds. A real hosted call made with a
  # placeholder fails fast at the provider, which is the desired signal to re-run `make seed` with keys.
  GROQ_API_KEY="${GROQ_API_KEY:-dev-placeholder-groq-key}"
  EMBEDDINGS_API_KEY="${EMBEDDINGS_API_KEY:-dev-placeholder-embeddings-key}"
  OPENAI_API_KEY="${OPENAI_API_KEY:-dev-placeholder-openai-key}"
  LANGSMITH_API_KEY="${LANGSMITH_API_KEY:-dev-placeholder-langsmith-key}"
  # Operator-dashboard secrets (004-evals-and-uis). Same env-forward-or-placeholder pattern. Dev defaults:
  #   OPERATOR_PASSWORD_HASH — bcrypt hash of the password "souschef-dev" (the `\$` keep the literal '$'
  #     segments out of shell expansion); DASHBOARD_COOKIE_KEY signs the login cookie; ADMIN_API_TOKEN is
  #     the shared bearer token the backend fails fast without.
  OPERATOR_PASSWORD_HASH="${OPERATOR_PASSWORD_HASH:-\$2b\$12\$krAZGw9bKfb8eWOFFCi8iuajtqTRzI9jrJ.PfEgGUiW/cTmy37eVe}"
  DASHBOARD_COOKIE_KEY="${DASHBOARD_COOKIE_KEY:-dev-placeholder-dashboard-cookie-key}"
  ADMIN_API_TOKEN="${ADMIN_API_TOKEN:-dev-placeholder-admin-api-token}"
  APP_SECRET="${APP_SECRET:-dev-placeholder-not-a-real-secret}"
else
  # ── Prod: require every MANDATORY secret from the operator's shell; never invent one. Collect all
  # missing names first so the operator sees the full list in one shot (FR-014: clear, actionable).
  _missing=""
  for _name in GROQ_API_KEY EMBEDDINGS_API_KEY OPERATOR_PASSWORD_HASH DASHBOARD_COOKIE_KEY ADMIN_API_TOKEN; do
    eval "_val=\${${_name}:-}"
    if [ -z "${_val}" ] || { echo "${_val}" | grep -q '^dev-placeholder-'; }; then
      _missing="${_missing} ${_name}"
    fi
  done
  if [ -n "${_missing}" ]; then
    echo "seed_vault: REFUSING to seed prod Vault at ${VAULT_ADDR} — missing real value(s) for:${_missing}" >&2
    echo "seed_vault: export each in your shell, then re-run (see contracts/secrets-keyspace.md R4). Nothing was written." >&2
    exit 1
  fi
  # Dormant-under-defaults keys: optional in prod, default to empty until their provider is activated.
  OPENAI_API_KEY="${OPENAI_API_KEY:-}"
  LANGSMITH_API_KEY="${LANGSMITH_API_KEY:-}"
  # app_secret is a generic signing secret; if the operator did not export one, generate a random value
  # rather than persist a known placeholder into prod.
  APP_SECRET="${APP_SECRET:-$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')}"
fi

# KV v2 data path is /v1/<mount>/data/<path>. Body wraps values under "data". The key names here are
# exactly what VaultAdapter.get(...) looks up at runtime (GROQ_API_KEY, EMBEDDINGS_API_KEY, OPENAI_API_KEY,
# LANGSMITH_API_KEY, and the operator-auth keys OPERATOR_PASSWORD_HASH, DASHBOARD_COOKIE_KEY, ADMIN_API_TOKEN).
curl -sf -X POST \
  -H "X-Vault-Token: ${VAULT_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"data\":{\"app_secret\":\"${APP_SECRET}\",\"GROQ_API_KEY\":\"${GROQ_API_KEY}\",\"EMBEDDINGS_API_KEY\":\"${EMBEDDINGS_API_KEY}\",\"OPENAI_API_KEY\":\"${OPENAI_API_KEY}\",\"LANGSMITH_API_KEY\":\"${LANGSMITH_API_KEY}\",\"OPERATOR_PASSWORD_HASH\":\"${OPERATOR_PASSWORD_HASH}\",\"DASHBOARD_COOKIE_KEY\":\"${DASHBOARD_COOKIE_KEY}\",\"ADMIN_API_TOKEN\":\"${ADMIN_API_TOKEN}\"}}" \
  "${VAULT_ADDR}/v1/secret/data/sous-chef" >/dev/null

if [ "${IS_LOCAL}" = "1" ]; then
  echo "seed_vault: wrote secret/sous-chef (local/dev placeholders unless overridden) to ${VAULT_ADDR}"
else
  echo "seed_vault: wrote secret/sous-chef (real operator-supplied secrets) to ${VAULT_ADDR}"
fi
