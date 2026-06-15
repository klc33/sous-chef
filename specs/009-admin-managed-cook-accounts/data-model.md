# Phase 1 — Data Model: Admin-Managed Cook Accounts

One new table (`cook_accounts`) plus a **re-key** of the existing cook-owned tables to reference it. Added by
Alembic `0005_cook_accounts` (head after 008's `0004_operator_accounts`). No change to `recipes`/embeddings,
nutrition, conversations, or 008's `operator_accounts`.

## New entity: `cook_accounts`

A named login identity for the cook-facing app. Replaces the passwordless `X-Profile-ID`. **No role** — every
cook account is simply a cook (the managing authority is an operator/admin from 008).

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | `Text` | PK | Opaque UUID4 string. This is the **owner key** threaded through the app (where `X-Profile-ID` was). Text (not native UUID) so the existing Text owner-key FKs align without a type migration. |
| `username` | `Text` | **UNIQUE**, NOT NULL | Login handle; case-sensitive. DB-enforced uniqueness (FR-006). |
| `display_name` | `Text` | NULL | Optional; defaults to `username` at the service layer. |
| `is_active` | `Boolean` | NOT NULL, default `true` | Deactivation flag (FR-009); disabled cooks are retained (no hard delete). |
| `password_hash` | `Text` | NOT NULL | **bcrypt** hash only (FR-014). Not a Vault secret. |
| `created_by` | `Text` | NULL | Username of the operator/admin (008) who created it. Audit (FR-016). |
| `created_at` | `TimestamptZ` | NOT NULL, server default `now()` | |
| `updated_at` | `TimestamptZ` | NOT NULL, server default `now()`, `onupdate now()` | Bumped on status/password change. |

**Indexes**: PK on `id`; UNIQUE index on `username` (the login lookup path).

ORM `app/models/cook_account.py` subclasses the existing `Base`, mirroring `app/models/profile.py` /
008's `operator_account.py`. No `CHECK role` constraint (cooks are role-less).

## Re-keyed entities (ownership moves to the cook account)

The owner key (`profile_id` today) **becomes the cook account id**. The column names stay, so downstream code
is unchanged; only the *source* of the value and one new FK change.

- **`profiles`** — `profile_id` (Text PK) is now a **FK → `cook_accounts.id`** (`ON DELETE CASCADE`). Still
  holds `diet` / `allergies` / `default_servings` (the constraints that drive the wall), now owned by the
  cook account. A row is created on the cook's first profile-write or favorite (`ensure_exists`), and the FK
  is satisfied because the cook is logged in (their `cook_accounts` row exists).
- **`favorites`** — unchanged shape: composite PK `(profile_id, recipe_id)`, `profile_id` FK →
  `profiles.profile_id` (`CASCADE`). Now transitively owned by the cook account.
- **`seen_history`** — unchanged shape: `profile_id` FK → `profiles.profile_id` (`CASCADE`). Now
  transitively owned by the cook account.

> Because `cook_accounts.id` is `Text` and the existing owner-key columns are `Text`, the re-key adds one FK
> without altering column types across three tables — the deliberately minimal-churn choice (research D1/D8).

## Validation rules (in `services/admin/cook_accounts.py` + `services/user/cook_auth.py`)

- **Unique username** — duplicate create rejected (DB UNIQUE backstop) → 409 (FR-006).
- **Password strength** — empty or `< 8` chars rejected on create and admin reset → 422 (FR-007/FR-010).
- **Generic auth failure** — `authenticate` returns `None` identically for unknown username and wrong
  password; dummy bcrypt verify on miss so timing doesn't leak existence → one generic 401 (FR-008).
- **Deactivation** — `is_active=false` blocks login and is re-checked per request by `require_cook` (FR-009).
- **Data isolation** — every cook read/write is scoped to the owner key from the token; a cook can never
  pass another cook's id (the id comes from the verified token, never the body/header) (FR-005).

## State transitions

```
        create (admin)                    reactivate (admin)
            │                                   │
            ▼                                   │
     ┌────────────┐   deactivate (admin)   ┌────┴───────────┐
 ───▶│  active     │──────────────────────▶│  disabled       │
     │ can sign in │◀──────────────────────│ cannot sign in  │
     └────────────┘                         └────────────────┘
```

- A disabled cook cannot obtain a token and loses access on the next request (per-request `is_active` check).
- **No delete** transition (deactivate-not-delete). Password reset is a self-edge (re-hash, bump
  `updated_at`), admin-only.

## Cook-session token (the "session", not a table)

Issued by `cook_auth` on login; verified by `require_cook`:

```json
{ "sub": "<cook_accounts.id>", "typ": "cook", "iat": 1718…, "exp": 1718…  /* iat + cook_jwt_ttl_minutes (8h) */ }
```

Signed HS256 with **`COOK_SESSION_KEY`** (Vault) — a *different* key from 008's operator `JWT_SIGNING_KEY`.
`require_cook` rejects any token that is not `typ:"cook"` or not signed by the cook key, so operator tokens
cannot be replayed here (FR-013). Authority on each request = valid signature + non-expired + `sub` account
exists and `is_active`.

## Seeded demo/eval cook (FR-020)

`bootstrap_demo_cook()` runs at startup (idempotent): if `username == DEMO_COOK_USERNAME` (config) does not
exist, create it from `DEMO_COOK_PASSWORD` (Vault). CI evals, the smoke test, and the live demo log in as
this account. Skipped if it already exists.

## Migration notes (`0005_cook_accounts`, down_revision `0004`)

- `upgrade()`: create `cook_accounts` (+ unique `username` index); **clear ownerless anonymous data** —
  `DELETE FROM seen_history; DELETE FROM favorites; DELETE FROM profiles;` (FR-015 fresh start); add FK
  `profiles.profile_id → cook_accounts.id` (`ON DELETE CASCADE`).
- `downgrade()`: drop the FK and `cook_accounts`. ⚠️ **Irreversible**: the cleared `profiles`/`favorites`/
  `seen_history` data is NOT restored by downgrade — back up before applying on any environment with real
  data (FR-015 fresh start; surfaced in RUNBOOK per task T029).
- Demo-cook seeding is **not** in the migration (needs Vault); it runs at app startup so a migrated-but-empty
  DB becomes demo-ready on first boot.
- `operator_accounts` (008) and all recipe/embedding tables are untouched.
