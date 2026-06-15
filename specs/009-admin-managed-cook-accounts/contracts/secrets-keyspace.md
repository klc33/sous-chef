# Contract — Vault Secrets Keyspace (delta for 009)

Secrets live at the existing KV v2 path `secret/sous-chef`, read by `app/infra/vault.py`, seeded by
`scripts/seed_vault.sh`. This feature **adds two keys** and **one non-secret config value**. It does not
touch 008's keys. `.env.example` carries names/notes only — never values (Constitution VI).

## ADD (Vault secrets)

| Vault key | Used by | Notes |
|---|---|---|
| `COOK_SESSION_KEY` | backend `core/security` (cook tokens) | HS256 signing/verification key for **cook-session** JWTs. **Distinct** from 008's `JWT_SIGNING_KEY` so the two token domains are isolated (FR-013). ≥32 random bytes. |
| `DEMO_COOK_PASSWORD` | backend `bootstrap_demo_cook()` | Plaintext initial password for the seeded demo/eval cook; bcrypt-hashed before storage, never persisted in clear. Consulted only when the demo cook does not yet exist. |

## ADD (non-secret config, NOT Vault — `app/config.py` / `.env.example`)

| Name | Default | Notes |
|---|---|---|
| `DEMO_COOK_USERNAME` | `demo` | Username for the seeded demo/eval cook (FR-020). Non-secret (the *password* is the secret). |
| `COOK_JWT_TTL_MINUTES` | `480` (8h) | Cook-session lifetime; mirrors 008. |

## KEEP (unchanged)

008's `JWT_SIGNING_KEY`, `BOOTSTRAP_ADMIN_PASSWORD`, `DASHBOARD_COOKIE_KEY`, and all provider keys are
unchanged. The retired `X-Profile-ID` was never a secret (no Vault impact).

## Seed-script changes (`scripts/seed_vault.sh`)

- **Local mode**: add env-forward-or-placeholder lines for `COOK_SESSION_KEY` (default
  `dev-placeholder-cook-session-key`) and `DEMO_COOK_PASSWORD` (default e.g. `souschef-demo`).
- **Prod mode**: add `COOK_SESSION_KEY` + `DEMO_COOK_PASSWORD` to the mandatory `_missing` loop so a prod
  seed fails fast without them. Extend the KV write body with both keys.
- CI: the eval/CI environment seeds these too (the demo cook is how CI authenticates — FR-020).

## Invariants (unchanged)

- No secret value in repo, image, `.env`, logs, or trace spans (redaction). A bcrypt hash is **not** a Vault
  secret — it lives in the `cook_accounts` row.
- A missing mandatory secret fails fast at startup / refuses to seed prod (existing behavior, extended to the
  new keys).
- The cook signing key and the operator signing key are **separate** Vault entries (FR-013).
