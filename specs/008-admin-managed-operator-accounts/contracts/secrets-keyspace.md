# Contract — Vault Secrets Keyspace (delta for 008)

All secrets live at the existing KV v2 path `secret/sous-chef` (mount `secret`, path `sous-chef`), read by
`app/infra/vault.py` and seeded by `scripts/seed_vault.sh`. This feature **adds two keys, keeps one, and
retires three**. `.env.example` carries key *names/notes* only — never values (Constitution VI).

## ADD

| Vault key | Kind | Used by | Notes |
|---|---|---|---|
| `JWT_SIGNING_KEY` | secret | backend `core/security` | HS256 signing/verification key for login JWTs. Generate ≥32 random bytes. Prod: required real value; local: env-forward-or-placeholder. |
| `BOOTSTRAP_ADMIN_PASSWORD` | secret | backend `bootstrap_admin()` | Plaintext initial password for the first admin; hashed with bcrypt before storage, never persisted in clear. Consulted only when the accounts table is empty. |

## ADD (non-secret config, NOT Vault)

These go in `app/config.py` / `.env.example`, not Vault (they are not credentials):

| Name | Default | Notes |
|---|---|---|
| `BOOTSTRAP_ADMIN_USERNAME` | `admin` | Username for the bootstrapped first admin. Non-secret (the *password* is the secret). |
| `JWT_TTL_MINUTES` | `480` (8h) | Token lifetime; matched to the dashboard cookie window. |

## KEEP

| Vault key | Why it stays |
|---|---|
| `DASHBOARD_COOKIE_KEY` | Still signs the dashboard session cookie that now carries the JWT (refresh-safe login, FR-013). |

## RETIRE (remove from seed script + VaultAdapter lookups + config)

| Removed | Replaced by |
|---|---|
| `OPERATOR_PASSWORD_HASH` (Vault) | Per-user bcrypt hashes in `operator_accounts.password_hash` (DB). |
| `OPERATOR_USERNAME` (env, `config.operator_username`) | `operator_accounts.username` rows; bootstrap uses `BOOTSTRAP_ADMIN_USERNAME`. |
| `ADMIN_API_TOKEN` (Vault) | Per-user JWT verified by `require_operator`/`require_admin`. |

## Seed-script changes (`scripts/seed_vault.sh`)

- **Local mode**: add env-forward-or-placeholder lines for `JWT_SIGNING_KEY` (default
  `dev-placeholder-jwt-signing-key`) and `BOOTSTRAP_ADMIN_PASSWORD` (default e.g. `souschef-dev`). Drop the
  `OPERATOR_PASSWORD_HASH` / `ADMIN_API_TOKEN` placeholder lines.
- **Prod mode**: replace `OPERATOR_PASSWORD_HASH ADMIN_API_TOKEN` in the mandatory `_missing` loop with
  `JWT_SIGNING_KEY BOOTSTRAP_ADMIN_PASSWORD`; keep `DASHBOARD_COOKIE_KEY`. The KV write body swaps the
  retired keys for the new ones.
- **Migration note (RUNBOOK)**: after deploy, the old single-operator credential and shared token no longer
  grant access; sign in as the bootstrap admin, create real accounts, then (optionally) rotate the bootstrap
  password by recreating it.

## Invariants (unchanged)

- No secret value in repo, image, `.env`, logs, or trace spans (redaction). A bcrypt hash is **not** a Vault
  secret — it is non-reversible and lives in the DB row.
- A missing mandatory secret fails fast at startup / refuses to seed prod (existing behavior, extended to
  the new keys).
