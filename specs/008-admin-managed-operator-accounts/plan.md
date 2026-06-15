# Implementation Plan: Admin-Managed Operator Accounts (JWT)

**Branch**: `008-admin-managed-operator-accounts` | **Date**: 2026-06-15 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/008-admin-managed-operator-accounts/spec.md`

## Summary

Replace the operator dashboard's **single hardcoded operator** (one `OPERATOR_USERNAME` + one Vault
`OPERATOR_PASSWORD_HASH`) and the **static shared `ADMIN_API_TOKEN`** with **named, admin-managed accounts
authenticated by a signed JWT**. Login validates a username/password against a durable `operator_accounts`
table (bcrypt hashes), then issues a short-lived JWT carrying `{sub, role, exp}`; the backend verifies that
token and enforces the **admin-vs-user** split server-side. An admin provisions / lists / deactivates /
reactivates accounts from a new dashboard **Users** page; there is **no self-service sign-up** anywhere; a
first **admin is bootstrapped** at startup from Vault so a fresh deploy always has a way in; the last active
admin can never be deactivated; and the dashboard keeps cookie-persisted sessions so a refresh does not log
you out.

**Scope boundary (must not break):** this is the **operator-console** boundary only. The cook-facing React
widget, its passwordless `X-Profile-ID` identity, `app/api/user/*`, and the public `/chat` path are
untouched. The wall, grounding, redaction, and red-team gates run identically and stay green. The change
lives entirely in the existing operator surface (`api/admin`, `services/admin`, `repo`, `models`, the
dashboard) plus one new `core/security` seam and one Alembic migration.

Concretely the work closes this delta from today's state:

- **Identity store**: today there is no account table; add `operator_accounts` (one migration; cook-side
  schema untouched).
- **Auth mechanism**: today the dashboard holds the password hash and sends a *static* shared token; move
  credential checking to the backend and replace the static token with a **per-user JWT** the backend mints
  and verifies — so the backend knows *who* is acting and their *role*.
- **Authorization**: today every `/admin/*` route is gated by one `require_operator` (token present = full
  access). Split into `require_operator` (any valid active account) and `require_admin` (role == admin),
  and gate the new `/admin/users/*` routes with `require_admin`.
- **Bootstrap & retirement**: seed the first admin from Vault on boot; retire `OPERATOR_USERNAME`,
  `OPERATOR_PASSWORD_HASH`, and `ADMIN_API_TOKEN`.

## Technical Context

**Language/Version**: Python 3.12 (backend + dashboard). No TypeScript anywhere (dashboard is Streamlit/
Python; the cook widget is untouched).

**Primary Dependencies**: FastAPI + Pydantic; SQLAlchemy + Alembic; PostgreSQL (psycopg); hvac (Vault);
**PyJWT** (new — issue/verify the login token, HS256); **bcrypt** (new — one-way password hashing on the
backend); Streamlit + streamlit-authenticator (reused only for its signed-cookie persistence); httpx (the
dashboard's existing backend client). **No torch/transformers in any image** (Constitution P10). Managed by
`uv`, both new deps in the **backend** optional group only (the dashboard image stays lean — it never hashes
or mints tokens; it just stores the JWT it is handed).

**Storage**: PostgreSQL — one new table `operator_accounts` on the existing `public` schema. No change to
`recipes / profiles / favorites / seen_history / conversations`. No Redis dependency (accounts are durable,
not session cache; the JWT itself is the session).

**Testing**: pytest — unit (account service rules + the security seam), integration (login → JWT → `/admin`
authz, deactivated-login-denied, non-admin-403), and a **regression guard** that the existing cook-facing
suites (chat_flow, favorites, freshness, wall, redaction, red-team) stay green and unchanged (SC-007).

**Target Platform**: the existing backend service (local docker-compose + Railway) and the Streamlit
dashboard service. No new service, no new datastore.

**Project Type**: Web-service monolith + sibling Streamlit dashboard. This feature is an operator-auth
upgrade over the existing layered backend (`api → services → repo → infra/core`).

**Performance Goals**: not a perf feature. Login is one bcrypt verify + one JWT sign (sub-100ms). bcrypt cost
factor kept at a sane default (≈12) so a login is fast but brute-force is dear.

**Constraints**: secrets only in Vault (JWT signing key, bootstrap admin credential, cookie key); password
hashes are one-way and live in the DB (a hash is not a recoverable secret, so it belongs in the row, not
Vault); role enforced server-side, not merely hidden in the UI; constant-time auth + a single generic 401
(no account enumeration); last-admin lockout impossible; redaction covers any auth log/span. Images stay lean
(< ~500MB; no torch). **Session clock**: the **JWT `exp` is authoritative** for session length (8h per
Clarifications); the dashboard cookie window MUST be ≥ the token TTL so the cookie never expires *before* a
still-valid token (avoids a surprise mid-session logout). `streamlit-authenticator`'s expiry is day-grained,
so set it to cover the 8h window (the token, not the cookie, ends the session).

**Scale/Scope**: a handful of operator accounts (this is an internal console, not a public sign-up system).
Scope is one table + one security seam + auth/users API + dashboard login & Users page + secrets/migration/
docs. Cook-facing surfaces are explicitly out of scope.

## Constitution Check

*GATE: must pass before Phase 0 and re-checked after Phase 1 design.*

| Principle | Gate | Verdict |
|-----------|------|---------|
| I. Simplicity | Monolith; no new service/datastore; one agent untouched. | **PASS** — one table + one security module + routes inside the existing `api/admin`; JWT is a library call, not infrastructure. |
| II. Build only what's required | Every task traces to an FR. | **PASS** — maps to FR-001..FR-020; self-service password change explicitly deferred (spec Assumptions). |
| III. Separation of concerns | `api → services → repo → infra`; repo is the only DB toucher; `services/admin` for operator logic. | **PASS** — accounts read/written only via `repo/operator_accounts.py`; rules in `services/admin/operator_accounts.py`; `api/admin` stays thin. |
| IV. Testability | Critical + safety behaviors gated/tested; adapters mockable. | **PASS** — last-admin guard, generic-failure, bootstrap idempotency, JWT verify, and authz are unit/integration tested; cook gates unchanged and asserted green. |
| V. Reproducibility | Fresh clone runs identically; Alembic; pinned deps. | **PASS** — one Alembic migration; bootstrap makes a fresh DB self-seed an admin; deps pinned via uv lock. |
| VI. Security & privacy by default | Vault secrets; redaction before logs+spans; parameterized queries; safe-by-default. | **PASS, strengthened** — per-user accountability, role enforced in code, generic auth errors, last-admin guard, JWT in Vault-signed token; no credential in logs/traces. |
| VII. Maintainability | Readable, consistent, documented. | **PASS** — mirrors existing model/repo/service/api patterns; SECURITY/RUNBOOK/DECISIONS updated. |
| VIII. Documentation-first | Spec before code; docs in sync. | **PASS** — spec + this plan precede code; the contracts pin the API/secret shapes. |
| IX. Spec-driven | specify → plan → tasks → implement, committed. | **PASS** — this is that flow. |
| X. No unnecessary tech | Approved stack; **note: "no full end-user auth"**. | **PASS — see note** — this is **operator-console** auth (already in the stack via streamlit-authenticator), NOT cook/end-user auth, which stays passwordless. PyJWT + bcrypt each earn their place via the multi-user, role-based requirement. Justified in Complexity Tracking. |
| Safety invariants | Wall, grounding, hosted-only inference, lean classifier. | **PASS** — none of these paths are touched; no model weights, no torch; the cook journey is untouched and gate-verified. |

**Result: PASS.** The single item worth recording is the relationship to Principle X's "no full end-user
authentication" — addressed in Complexity Tracking below: this feature does **not** introduce end-user auth;
it upgrades the *operator* boundary that the constitution already sanctions.

## Project Structure

### Documentation (this feature)

```text
specs/008-admin-managed-operator-accounts/
├── spec.md              # WHAT & WHY (from /speckit-specify)
├── plan.md              # This file
├── plan-commands.md      # Paste-ready specify/plan/tasks blocks (Phase-8 style)
├── research.md          # Phase 0: JWT vs session, password hashing, bootstrap, token-storage decisions
├── data-model.md        # Phase 1: operator_accounts schema, JWT claim shape, state transitions
├── quickstart.md        # Phase 1: how to verify (bootstrap login → create user → role/deny checks)
├── contracts/           # Phase 1: the auth/authz/secret contracts
│   ├── auth-api.md       #   POST /admin/auth/login, GET /admin/auth/me, /admin/users/* shapes
│   ├── authz-model.md    #   require_operator vs require_admin; JWT claims; failure codes
│   └── secrets-keyspace.md  # Vault keys added/kept/retired for this feature
└── checklists/
    └── requirements.md  # Spec quality checklist (from /speckit-specify)
```

### Source Code (repository root) — files this feature adds or changes

```text
sous-chef/
├── pyproject.toml                       # CHANGE: backend group gains pyjwt + bcrypt (uv add --optional backend)
├── app/
│   ├── config.py                        # CHANGE: add VAULT_KEY_JWT_SIGNING_KEY, VAULT_KEY_BOOTSTRAP_ADMIN_*,
│   │                                    #   jwt_ttl_minutes + bootstrap_admin_username (non-secret);
│   │                                    #   deprecate OPERATOR_USERNAME / VAULT_KEY_OPERATOR_PASSWORD_HASH /
│   │                                    #   VAULT_KEY_ADMIN_API_TOKEN (kept only until callers are migrated)
│   ├── main.py                          # CHANGE: call bootstrap_admin() in lifespan startup; mount auth+users routers
│   ├── core/
│   │   └── security.py                  # ADD: hash_password/verify_password (bcrypt); issue_token/decode_token (PyJWT)
│   ├── models/
│   │   └── operator_account.py          # ADD: OperatorAccount ORM model
│   ├── repo/
│   │   └── operator_accounts.py         # ADD: the ONLY DB access for accounts (get/list/create/set_active/set_password/counts)
│   ├── services/admin/
│   │   └── operator_accounts.py         # ADD: create/list/deactivate/reactivate/reset_password/authenticate/bootstrap_admin
│   ├── schemas/
│   │   └── operator.py                  # ADD: LoginRequest, TokenResponse, CreateUserRequest, ResetPasswordRequest, AccountView (Pydantic)
│   └── api/
│       ├── admin_deps.py                # CHANGE: replace static-token check with JWT → require_operator + require_admin
│       └── admin/
│           ├── __init__.py              # CHANGE: register auth + users routers
│           ├── auth.py                  # ADD: POST /admin/auth/login, GET /admin/auth/me (login is unauthenticated)
│           └── users.py                 # ADD: /admin/users create/list/deactivate/reactivate/reset-password — all require_admin
├── alembic/versions/
│   └── 0004_operator_accounts.py        # ADD: create operator_accounts (cook-side schema untouched)
├── dashboard/
│   ├── auth.py                          # CHANGE: login posts creds → backend, stores JWT in signed cookie;
│   │                                    #   admin_client() sends Bearer JWT; add require_admin(); polished login UI
│   └── pages/
│       └── 4_users.py                   # ADD: admin-only Users page (create/list/deactivate/reactivate/reset-password; no signup)
├── scripts/
│   └── seed_vault.sh                    # CHANGE: seed JWT_SIGNING_KEY + BOOTSTRAP_ADMIN_* ; drop retired keys
├── .env.example                         # CHANGE: note new non-secret knobs; remove OPERATOR_USERNAME note
├── tests/
│   ├── unit/
│   │   ├── test_operator_accounts.py    # ADD: dedupe/weak-pw/deactivate/last-admin/bootstrap/generic-failure/reset-password
│   │   └── test_security.py             # ADD: bcrypt roundtrip; JWT issue→decode; expired/tampered rejected
│   └── integration/
│       └── test_operator_auth.py        # ADD: login→JWT→/admin ok; non-admin→403; deactivated→login denied
└── docs/
    ├── SECURITY.md                      # CHANGE: JWT model, role boundary, bootstrap, retired shared token
    ├── RUNBOOK.md                       # CHANGE: seed bootstrap admin; how an admin manages users
    └── DECISIONS.md                     # CHANGE: why JWT + per-user roles over the single shared token
```

**Structure Decision**: No new top-level structure. The feature slots into the existing layered monolith —
a new model, repo, service (under `services/admin`, the operator audience), thin `api/admin` routers, and
one cross-cutting `core/security` seam — exactly mirroring how 002/004 added their slices. The dashboard
changes stay inside the existing `dashboard/` surface.

## Complexity Tracking

> Filled because the Constitution Check flagged one item to justify (Principle X wording).

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| New deps **PyJWT** + **bcrypt** in the backend image | The feature requires per-user credentials (one-way hashing) and a verifiable, role-bearing session token across the dashboard↔backend boundary. bcrypt is already the dashboard's hashing scheme; PyJWT is a tiny pure-Python lib. | Reusing the *static shared token* cannot identify the user or carry a role (the whole point); a home-rolled HMAC token would re-implement JWT worse; storing the role only in a cookie the client controls is forgeable. |
| "No full end-user authentication" (P10) appears adjacent | This is **operator-console** auth, explicitly part of the approved stack (`streamlit-authenticator` for the dashboard). It upgrades who-can-operate; it does **not** add authentication to the cook/end-user path, which stays the passwordless profile-ID. | Keeping the single shared operator credential fails the actual requirement (grant/revoke per person, admin vs user). Adding cook auth would violate P10 — and is explicitly out of scope here. |
