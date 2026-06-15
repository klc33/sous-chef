# Phase 1 — Data Model: Admin-Managed Operator Accounts

One new table on the existing `public` schema. **No change** to `recipes / profiles / favorites /
seen_history / conversations` — the cook side is untouched (FR-019). Added by Alembic migration
`0004_operator_accounts` (current head is `0003_embeddings`).

## Entity: `operator_accounts`

A named login identity for the operator dashboard. Replaces the single `OPERATOR_USERNAME` +
`OPERATOR_PASSWORD_HASH`.

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | `UUID` | PK, default `uuid4` | Surrogate key (matches `seen_history.id` style). |
| `username` | `Text` | **UNIQUE**, NOT NULL | The login handle; case-sensitive match. Uniqueness enforced by a DB constraint (FR-009), not just app code. |
| `display_name` | `Text` | NULL | Optional human label; defaults to `username` at the service layer when blank. |
| `role` | `Text` | NOT NULL, `CHECK role IN ('admin','user')` | The permission level (FR-005). CHECK mirrors the `Diet` enum pattern in `profiles`. |
| `is_active` | `Boolean` | NOT NULL, default `true` | Deactivation flag (FR-008); disabled accounts are retained for audit, never hard-deleted. |
| `password_hash` | `Text` | NOT NULL | **bcrypt** hash only — never plaintext, never recoverable (FR-016). Not a Vault secret (a hash is not a key). |
| `created_by` | `Text` | NULL | Username of the admin who created the row; NULL for the bootstrap admin. Audit trail (FR-018). |
| `created_at` | `TimestamptZ` | NOT NULL, server default `now()` | |
| `updated_at` | `TimestamptZ` | NOT NULL, server default `now()`, `onupdate now()` | Bumped on role/status/password change. |

**Indexes**: PK on `id`; UNIQUE index on `username` (also the lookup path for login).

### ORM (`app/models/operator_account.py`)

Subclasses the existing `Base` (so Alembic autogenerate sees it), using `Mapped`/`mapped_column` like
`app/models/profile.py`. `__table_args__` carries the `CHECK (role IN ('admin','user'))` constraint named
`ck_operator_accounts_role`.

## Validation rules (enforced in `services/admin/operator_accounts.py`)

- **Unique username** — creation rejects a duplicate before insert and relies on the DB UNIQUE constraint as
  the backstop (race-safe); surfaces a clear 409/validation error (FR-009).
- **Password strength** — reject empty or `< 8` characters on create (FR-010). (Minimum length is the v1
  policy; documented in spec Assumptions.)
- **Role** — must be exactly `admin` or `user`; anything else is a validation error.
- **Last-admin guard** — `deactivate` refuses when the target is the only active admin (FR-015).
- **Generic auth failure** — `authenticate` returns `None` identically for unknown username and wrong
  password; the API maps that to one generic 401 (FR-012).
- **Admin password reset** — `reset_password(username, new_password)` re-hashes (bcrypt) and replaces
  `password_hash`, bumping `updated_at`; the new password is subject to the same `< 8`-char rejection as
  creation (FR-008a / FR-010). Admin-only; no self-service change in v1.

## State transitions

```
            create (admin)                       reactivate (admin)
                │                                      │
                ▼                                      │
        ┌───────────────┐   deactivate (admin, not    ┌┴──────────────┐
  ─────▶│  active        │──── last active admin) ────▶│  disabled      │
        │  can sign in   │◀───────────────────────────│  cannot sign in│
        └───────────────┘                              └───────────────┘
```

- A disabled account **cannot obtain a new token** (login denied) and **loses access on its next request**
  (each `require_*` re-checks `is_active`) — see research D2.
- There is **no delete** transition in v1 (deactivate-not-delete, spec Assumptions).

## JWT claim shape (the "session", not a table)

Issued by `core/security.issue_token(account)` on successful login; verified by `decode_token`:

```json
{
  "sub": "<username>",
  "role": "admin | user",
  "iat": 1718<...>,
  "exp": 1718<...>           // iat + jwt_ttl_minutes (default 8h)
}
```

Signed HS256 with `JWT_SIGNING_KEY` (Vault). The token is **not** persisted server-side; authority on each
request = valid signature + non-expired + the `sub` account still exists and `is_active`.

## Bootstrap record

On first run (`bootstrap_admin()`), exactly one row is created with `role='admin'`, `created_by=NULL`,
`username = BOOTSTRAP_ADMIN_USERNAME` (Vault), `password_hash = bcrypt(BOOTSTRAP_ADMIN_PASSWORD)` (Vault).
Skipped entirely if any account already exists (idempotent — FR-014).

## Migration notes (`0004_operator_accounts`)

- `upgrade()`: `create_table('operator_accounts', ...)` with the columns/constraints above; create the
  UNIQUE index on `username`.
- `downgrade()`: `drop_table('operator_accounts')`.
- Cook-side tables are not referenced. No FK to `profiles` (operators are a separate identity domain).
- Bootstrap is **not** performed in the migration (it needs Vault); it runs at app startup so a migrated-but-
  empty DB self-seeds on first boot.
