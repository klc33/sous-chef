# Phase 0 — Research & Decisions: Admin-Managed Operator Accounts (JWT)

All decisions are scoped to the **operator console**. The cook widget and public `/chat` are out of scope.

## D1 — Session mechanism: JWT (chosen) vs server-side sessions vs static token

- **Decision**: A signed **JWT** (HS256) minted by the backend on successful login, carrying
  `{sub: username, role, iat, exp}`. The dashboard stores it and presents it as `Authorization: Bearer
  <jwt>` on every `/admin/*` call.
- **Rationale**: The requirement is per-user identity **and** a role the backend can trust. A JWT carries
  both, is self-contained (no session store to add — keeps Constitution I/X lean), and lets the backend
  authorize *who* is acting. It is the explicit ask from the user ("auth should use JWT token").
- **Alternatives considered**:
  - *Static shared token (today)* — cannot identify the user or carry a role; rejected (it is what we
    replace).
  - *Server-side session table / Redis sessions* — adds a store and a lookup on every request; Redis is
    already optional in this project, so leaning on it for auth would make auth fragile. Rejected for scope.
  - *RS256 (asymmetric)* — useful when many independent services verify tokens; here one backend both signs
    and verifies, so a single HS256 secret in Vault is simpler and sufficient.

## D2 — Token lifetime & revocation

- **Decision**: Short-lived JWT (**default 8h `exp`**, config `jwt_ttl_minutes`), matched to the dashboard
  cookie window. Revocation is **deactivation-driven**: every `require_*` dependency re-checks that the
  account still exists and `is_active` on each request, so a deactivated user is denied at their next call
  even if their JWT has not yet expired.
- **Rationale**: A stateless JWT cannot be "deleted", but re-validating the account row on each request
  gives immediate revocation without a token blacklist — simplest design that satisfies FR-008/FR-015 and
  the "deactivated user denied" edge case. The DB hit is one indexed lookup the admin routes already do.
- **Alternatives considered**: a token denylist (more moving parts, needs a store); very short TTL +
  refresh tokens (overkill for an internal console). Both rejected per Constitution I.

## D3 — Password hashing: bcrypt

- **Decision**: **bcrypt** (cost ≈ 12) via the `bcrypt` library on the backend; store only the hash in
  `operator_accounts.password_hash`.
- **Rationale**: bcrypt is already the dashboard's hashing scheme (streamlit-authenticator), so hashes are
  conceptually consistent and reviewers know it; it is slow-by-design and salted. A hash is not a
  recoverable secret, so it lives in the DB row, not Vault.
- **Alternatives considered**: argon2 (excellent, but adds a heavier dep for no requirement here); plain
  PBKDF2 (weaker ecosystem fit). Rejected per "no unnecessary tech".

## D4 — Where credential checking happens: backend, not dashboard

- **Decision**: The **backend** owns credential verification and token issuance (`POST /admin/auth/login`).
  The dashboard posts username/password, receives a JWT, and **never holds a password hash**.
- **Rationale**: Centralizes the auth boundary in one tested place, keeps the dashboard image lean (no
  bcrypt/PyJWT there), and removes secret material from the Streamlit surface. Matches the existing
  "dashboard is a thin client over `/admin/*`" posture.
- **Alternatives considered**: keep auth in streamlit-authenticator against a multi-user credential file —
  rejected: it would re-introduce credentials into the dashboard and couldn't enforce role server-side.

## D5 — Dashboard token storage & refresh-safe session

- **Decision**: Store the JWT inside the **existing signed session cookie** (the `streamlit-authenticator`
  cookie key already in Vault). On each Streamlit rerun/refresh, re-hydrate the JWT from the cookie; if
  present and not expired, the operator stays logged in (FR-013). Logout clears the cookie.
- **Rationale**: Reuses the one piece of streamlit-authenticator that solved the original "refresh logs you
  out" problem, without keeping its credential-checking role. No new cookie library (Constitution X).
- **Alternatives considered**: `st.session_state` only (cleared on full reload — the exact bug we must not
  reintroduce); a separate cookie manager dep (unnecessary).

## D6 — Authorization split: `require_operator` vs `require_admin`

- **Decision**: Two FastAPI dependencies. `require_operator` accepts any valid, non-expired JWT for an
  **active** account (gates all existing `/admin/*` operational routes). `require_admin` additionally
  asserts `role == "admin"` (gates every `/admin/users/*` route). Login is the only unauthenticated
  `/admin` endpoint.
- **Rationale**: Role is enforced in code on the server, not merely hidden in the UI (FR-011 / SC-005). The
  split keeps existing operator routes working for any operator while fencing user-management to admins.
- **Alternatives considered**: a single dependency with per-route role params (less explicit, easy to get
  wrong); UI-only hiding (forgeable). Rejected.

## D7 — Bootstrap admin (first-run, idempotent)

- **Decision**: On startup (`app/main.py` lifespan), call `bootstrap_admin()`: if **no account exists**,
  create one admin from `BOOTSTRAP_ADMIN_USERNAME` + `BOOTSTRAP_ADMIN_PASSWORD` (Vault). If any account
  already exists, do nothing.
- **Rationale**: Guarantees a fresh deploy always has a way in (FR-014) and is reproducible (Constitution
  V). Idempotent so restarts never create a second admin or reset a changed password.
- **Alternatives considered**: a one-off seed script only (a fresh prod Vault + empty DB would have no
  admin until someone remembers to run it — fragile for the demo). Startup bootstrap is safer; the seed
  script still *populates Vault* with the bootstrap credential.
- **Password recovery**: self-service password change is deferred (spec Assumptions), but an admin can reset
  any account's password via `reset_password` (FR-008a) — including rotating the bootstrap admin's initial
  password after first login.

## D8 — Last-admin lockout protection

- **Decision**: `deactivate` refuses when the target is the **last active admin** (`count_active_admins() <=
  1` and target is that admin). Same guard prevents an admin from disabling their own last-admin account.
- **Rationale**: Administration must never be lockable-out (FR-015 / SC-006). Deterministic guard in the
  service, unit-tested.

## D9 — Account enumeration resistance

- **Decision**: A wrong password and a non-existent username return the **same** generic 401
  ("Invalid username or password"); `verify_password` runs (against a dummy hash when the user is missing)
  so timing does not leak existence.
- **Rationale**: FR-012 / SC — login must not reveal which usernames exist.

## D10 — Retiring the old credentials

- **Decision**: Remove `OPERATOR_USERNAME`, `OPERATOR_PASSWORD_HASH`, and `ADMIN_API_TOKEN` once callers are
  migrated. The dashboard and `admin_deps` switch to JWT in the same change; the seed script stops seeding
  the retired keys. Documented in RUNBOOK as a deploy note (old credential stops granting access).
- **Rationale**: FR-017 — the new model *replaces* the old one; leaving the static token alive would be a
  second, weaker door.

## Open clarifications

None blocking. Deferred-by-design (recorded in spec Assumptions): self-service password change/reset,
SSO/MFA, multiple admin tiers, hard-delete of accounts.
