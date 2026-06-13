# Contract: Secrets Keyspace

Defines exactly which values are secret, where each lives, and the inspection that proves it. Verifies
FR-004, FR-005, FR-006, FR-014, SC-004, US4.

## The two stores (no overlap)

1. **Railway variables** — bootstrap + non-secret only: `ENV`, `VAULT_ADDR`, `VAULT_TOKEN`,
   `POSTGRES_URL`*, `REDIS_URL`*, `PHOENIX_COLLECTOR_ENDPOINT`, `LLM_PROVIDER` + model knobs,
   `WIDGET_ORIGINS`, `BACKEND_ADMIN_URL`, `OPERATOR_USERNAME`. (* = platform-injected by the plugin.)
2. **Vault** `secret/sous-chef` (KV v2) — the **only** home for app secrets: `GROQ_API_KEY`,
   `EMBEDDINGS_API_KEY`, `OPENAI_API_KEY`, `OPERATOR_PASSWORD_HASH`, `DASHBOARD_COOKIE_KEY`,
   `ADMIN_API_TOKEN`, `app_secret`.

## Rules (must all hold)
- **R1**: No Vault-key value appears in the repository, any committed `.env`, or any built image. `.env`
  is gitignored; `.env.example` contains placeholders/addresses only (FR-006).
- **R2**: The running app reads every app secret from Vault at startup (`app/infra/vault.py`); a missing
  required secret (e.g. `ADMIN_API_TOKEN`) raises `StartupConfigError` and the app fails fast (FR-014).
- **R3**: Managed datastore credentials (`POSTGRES_URL`, `REDIS_URL`) come from the platform injection,
  not from Vault and not hardcoded (FR-005).
- **R4**: The production Vault is seeded **once** by the operator with real keys exported in their shell
  (`scripts/seed_vault.sh` against the prod `VAULT_ADDR`); keys persist on Vault's volume and are never
  put into Railway variables.

## Inspection (the SC-004 test)
- `git grep` / image scan for the known key shapes (`gsk-`, `sk-`, bearer/Vault-token shapes) returns
  **zero** hits in repo and image.
- At runtime, the app obtains secrets from Vault (observable: removing a Vault key makes startup fail with
  a clear message), and datastore URLs resolve from platform-injected variables.
