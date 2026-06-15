---
description: "Task list for 008-admin-managed-operator-accounts"
---

# Tasks: Admin-Managed Operator Accounts (JWT)

**Input**: Design documents from `specs/008-admin-managed-operator-accounts/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: INCLUDED — this project is test-gated (Constitution P4; spec Testing + SC-007 regression). Write
each story's tests before its implementation and confirm they fail first.

**Scope guard on EVERY task**: operator-console only. Never touch `app/api/user/*`, the cook widget, the
public `/chat` path, or the wall/grounding/redaction/red-team code. SC-007 (cook suites stay green) is the
acceptance backstop.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 / US2 / US3 (Setup & Foundational & Polish carry no story label)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: dependencies and non-secret config the rest of the feature builds on.

- [ ] T001 Add backend deps via `uv add --optional backend pyjwt bcrypt` (updates `pyproject.toml` + `uv.lock`); confirm no torch and the dashboard group is unchanged
- [ ] T002 [P] Add config in `app/config.py`: constants `VAULT_KEY_JWT_SIGNING_KEY = "JWT_SIGNING_KEY"` and `VAULT_KEY_BOOTSTRAP_ADMIN_PASSWORD = "BOOTSTRAP_ADMIN_PASSWORD"`; settings `bootstrap_admin_username` (default `"admin"`) and `jwt_ttl_minutes` (default `480` = 8h per Clarifications); mark `operator_username` / `VAULT_KEY_OPERATOR_PASSWORD_HASH` / `VAULT_KEY_ADMIN_API_TOKEN` as deprecated (removed in Polish)
- [ ] T003 [P] Update `.env.example`: add notes for `BOOTSTRAP_ADMIN_USERNAME` and `JWT_TTL_MINUTES` (non-secret); remove the `OPERATOR_USERNAME` note; never list secret values

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the shared auth backbone every story needs — model, storage, the security seam, the account
service (all business rules), and the JWT authorization split. Per the plan, the account service serves all
three stories, so it lives here.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T004 Create `OperatorAccount` ORM model in `app/models/operator_account.py` per [data-model.md](data-model.md) (UUID id, unique username, display_name, role with `CHECK role IN ('admin','user')`, is_active default true, password_hash, created_by, created_at/updated_at)
- [ ] T005 Register the model in `app/models/__init__.py` so Alembic autogenerate sees it (depends on T004)
- [ ] T006 Create Alembic migration `alembic/versions/0004_operator_accounts.py` (down_revision `0003`): create `operator_accounts` + UNIQUE index on `username`; `downgrade()` drops the table; do not reference any cook-side table (depends on T004, T005)
- [ ] T007 [P] Create Pydantic schemas in `app/schemas/operator.py`: `LoginRequest`, `TokenResponse`, `CreateUserRequest`, `ResetPasswordRequest`, `AccountView` (AccountView NEVER serializes `password_hash`) per [contracts/auth-api.md](contracts/auth-api.md)
- [ ] T008 [P] Create the security seam in `app/core/security.py`: `hash_password`/`verify_password` (bcrypt, cost ≈12), `issue_token(account)`→JWT `{sub,role,iat,exp}` (HS256, exp = now + `jwt_ttl_minutes`), `decode_token(token)` (raise on bad signature/expiry); signing key read from Vault `JWT_SIGNING_KEY`
- [ ] T009 Create the repo in `app/repo/operator_accounts.py` (the ONLY DB access for accounts; ORM/parameterized): `get_by_username`, `list_all`, `create`, `set_active(username, bool)`, `set_password(username, hash)`, `count_active_admins`, `exists_any` (depends on T004)
- [ ] T010 Create the service in `app/services/admin/operator_accounts.py`: `create_account` (reject duplicate username → 409, reject `<8`-char/empty password → 422, bcrypt-hash), `list_accounts`, `deactivate`/`reactivate` (refuse deactivating the last active admin via `count_active_admins`), `reset_password` (same `<8` rule), `authenticate` (constant-time, dummy-verify on unknown user, generic failure), `bootstrap_admin` (no-op if `exists_any`, else create one admin from Vault creds); emit audit log lines with NO password/hash/secret (depends on T007, T008, T009)
- [ ] T011 Rewrite `app/api/admin_deps.py`: replace the static `ADMIN_API_TOKEN` compare with JWT validation — `require_operator` (valid, non-expired token whose `sub` account exists and is active → else 401 `admin_unauthorized`) and `require_admin` (additionally `role == 'admin'` → else 403 `admin_forbidden`), per [contracts/authz-model.md](contracts/authz-model.md) (depends on T008, T009)
- [ ] T012 Update secrets plumbing: `scripts/seed_vault.sh` adds `JWT_SIGNING_KEY` + `BOOTSTRAP_ADMIN_PASSWORD` (local placeholders + prod mandatory loop), drops `OPERATOR_PASSWORD_HASH`/`ADMIN_API_TOKEN`, keeps `DASHBOARD_COOKIE_KEY`; align `app/infra/vault.py` lookups per [contracts/secrets-keyspace.md](contracts/secrets-keyspace.md) (depends on T002)
- [ ] T013 [P] Unit tests in `tests/unit/test_security.py`: bcrypt hash↔verify roundtrip; JWT `issue_token`→`decode_token` claims; expired token rejected; tampered/wrong-key signature rejected (depends on T008)
- [ ] T014 [P] Unit tests in `tests/unit/test_operator_accounts.py`: duplicate-username reject, weak/empty password reject (create AND reset), deactivate, last-active-admin guard refuses, reactivate, `reset_password` changes the hash, `bootstrap_admin` idempotency, `authenticate` generic failure for unknown user vs wrong password (depends on T010)
- [ ] T014a [P] Unit test in `tests/unit/test_operator_accounts.py` for FR-018 audit: create/deactivate/reset emit an audit log line recording the action + actor, and assert the captured log/structlog event contains NO password, hash, or token value (depends on T010)

**Checkpoint**: auth backbone ready — stories can begin.

---

## Phase 3: User Story 1 - Admin provisions and revokes accounts (Priority: P1) 🎯 MVP

**Goal**: an admin can create, list, deactivate, reactivate, and reset-password operator accounts; no
self-service creation exists.

**Independent Test**: mint an admin JWT directly via `app/core/security.issue_token` (no login endpoint
needed) and exercise `/admin/users/*` — create (201), duplicate (409), weak password (422), list, deactivate
(200) / last-admin (409), reactivate (200), reset-password (200, old password then fails); a `user`-role
token gets 403 on every `/admin/users/*` route.

### Tests for User Story 1 ⚠️ (write first, ensure they fail)

- [ ] T015 [P] [US1] Integration test `tests/integration/test_operator_users.py`: drive the full `/admin/users/*` surface with a minted admin token (create/list/deactivate/reactivate/reset-password + the 409/422/404 cases) and assert a `user`-role token → 403 (depends on Phase 2)

### Implementation for User Story 1

- [ ] T016 [US1] Implement `app/api/admin/users.py`: `POST /admin/users`, `GET /admin/users`, `POST /admin/users/{username}/deactivate`, `/reactivate`, `/reset-password` — all gated by `require_admin`; map service errors to the codes in [contracts/auth-api.md](contracts/auth-api.md) (depends on T010, T011, T007)
- [ ] T017 [US1] Register the users router in `app/api/admin/__init__.py` `register_admin_routers` (depends on T016)

**Checkpoint**: the account-management API is fully functional and independently testable via a minted token.

---

## Phase 4: User Story 2 - Provisioned operator signs in + role-aware console (Priority: P2)

**Goal**: a provisioned operator signs in through a professional login screen (no sign-up), stays signed in
across refresh, and a non-admin cannot see or reach the Users area; the admin-only Users page surfaces the
US1 API in the dashboard.

**Independent Test**: sign in via the login screen → reach the console; refresh → still signed in; a non-admin
account sees no Users page and a non-admin token gets 403 from `/admin/users/*`; there is no registration
control anywhere.

### Tests for User Story 2 ⚠️ (write first, ensure they fail)

- [ ] T018 [P] [US2] Integration test `tests/integration/test_operator_auth.py`: `POST /admin/auth/login` returns a JWT + role for valid creds; wrong password AND unknown username both → identical generic 401; a deactivated account → login denied; `GET /admin/auth/me` echoes the token's claims (depends on Phase 2)

### Implementation for User Story 2

- [ ] T019 [US2] Implement `app/api/admin/auth.py`: `POST /admin/auth/login` (unauthenticated — `authenticate` then `issue_token`; generic 401 on failure) and `GET /admin/auth/me` (`require_operator`, echoes `{username, role}`) (depends on T010, T008, T011)
- [ ] T020 [US2] Register the auth router in `app/api/admin/__init__.py` (depends on T019)
- [ ] T021 [US2] Rewrite `dashboard/auth.py`: a polished/professional login screen that POSTs creds to `/admin/auth/login`, stores the returned JWT in the signed cookie (reuse the Vault `DASHBOARD_COOKIE_KEY`) so refresh re-hydrates; remove all password-hash handling from the dashboard; `admin_client()` attaches `Authorization: Bearer <jwt>`; add `require_admin()` helper. **Session clock**: the JWT `exp` (8h) is authoritative — set the cookie expiry ≥ the token TTL (day-grained `streamlit-authenticator` value chosen to cover 8h) so the cookie never lapses before a valid token; treat an expired/absent token as logged-out regardless of the cookie (depends on T019)
- [ ] T022 [US2] Add the admin-only Users page `dashboard/pages/4_users.py`: gated by `require_admin()`; create-account form (username, initial password, role), accounts table (username/role/status), and deactivate/reactivate/reset-password actions calling the US1 API via `admin_client()`; NO sign-up control (depends on T021, T016)

**Checkpoint**: the dashboard login + role-aware console work; refresh-safe; non-admins are fenced out.

---

## Phase 5: User Story 3 - Sessions persist & access is always recoverable (Priority: P3)

**Goal**: a fresh deployment always has a working bootstrapped admin; bootstrap never duplicates; the last
active admin can never be deactivated; deactivation revokes access promptly.

**Independent Test**: on an empty accounts table, startup seeds exactly one admin who can log in; restart
creates no second admin; deactivating the only admin → 409; a deactivated account's existing token is rejected
on its next request.

### Tests for User Story 3 ⚠️ (write first, ensure they fail)

- [ ] T023 [P] [US3] Integration test `tests/integration/test_operator_bootstrap.py`: fresh DB → `bootstrap_admin` (via app startup/lifespan) yields one admin that can log in; a second startup adds no account; last-active-admin deactivate → 409; a token for a now-deactivated account → 401 on a protected route (depends on Phase 2)

### Implementation for User Story 3

- [ ] T024 [US3] Wire `bootstrap_admin()` into the app startup lifespan in `app/main.py` (runs once after Vault load + DB ready; idempotent), reading `BOOTSTRAP_ADMIN_USERNAME` (config) + `BOOTSTRAP_ADMIN_PASSWORD` (Vault) (depends on T010)

**Checkpoint**: continuity + lockout safety guaranteed; all three stories independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: retire the old credential model, document, and prove nothing cook-facing regressed.

- [ ] T025 Retire the legacy single-operator path: remove `operator_username` + `VAULT_KEY_OPERATOR_PASSWORD_HASH` + `VAULT_KEY_ADMIN_API_TOKEN` usages from `app/config.py`, `app/api/admin_deps.py`, and any remaining references (per [contracts/secrets-keyspace.md](contracts/secrets-keyspace.md)) — confirm nothing still reads them
- [ ] T026 [P] Update `docs/SECURITY.md`: the JWT model, role boundary (`require_operator` vs `require_admin`), bootstrap, and the retired shared token
- [ ] T027 [P] Update `docs/RUNBOOK.md`: seed the bootstrap-admin Vault secrets; how an admin provisions / deactivates / resets users; deploy note that the old single-operator credential stops granting access
- [ ] T028 [P] Update `docs/DECISIONS.md`: why per-user JWT + roles over the single shared token (carry the trade-off)
- [ ] T029 Run `make lint && make test && make evals`; confirm all green INCLUDING the unchanged cook suites (chat_flow, favorites, freshness, wall) and the red-team + redaction gates (SC-007)
- [ ] T030 Run the [quickstart.md](quickstart.md) steps 1–7 against `make up` to validate the feature end-to-end on the running stack

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (P1)**: no dependencies.
- **Foundational (P2)**: depends on Setup — **blocks all stories**.
- **User Stories (P3–P5)**: each depends only on Foundational; US1/US2/US3 are otherwise independent at the
  API/test level. One real integration seam: US2's dashboard **Users page** (T022) surfaces US1's API
  (T016), so run T016 before T022.
- **Polish (P6)**: after the stories you intend to ship.

### Within each story

- Write the story's test (T015 / T018 / T023) first and see it fail, then implement.
- Models → repo → service (all in Foundational) → endpoints (stories) → dashboard.

### Parallel opportunities

- Setup: T002, T003 in parallel.
- Foundational: T007 + T008 in parallel; T013 + T014 in parallel after their targets exist.
- Across stories (after Foundational): T015, T018, T023 can be written in parallel; US1 and US3 backend work
  is independent of US2; only T022 waits on T016.

---

## Parallel Example: Foundational

```bash
# After T004–T006 (model + migration), these touch different files:
Task: "Create Pydantic schemas in app/schemas/operator.py"          # T007
Task: "Create the security seam in app/core/security.py"            # T008
# Then, after the service/seam land:
Task: "Unit tests in tests/unit/test_security.py"                   # T013
Task: "Unit tests in tests/unit/test_operator_accounts.py"          # T014
```

---

## Implementation Strategy

### MVP scope

- **Backend MVP = US1** (account-management API), independently testable via a minted admin token.
- **Demoable MVP = US1 + US2**: the dashboard login UI (US2) is what makes US1 usable by a human, since an
  admin-only page requires the login plumbing. Ship Setup → Foundational → US1 → US2 for the first
  end-to-end slice, then add US3 (bootstrap + continuity) and Polish.

### Incremental delivery

1. Setup + Foundational → auth backbone ready.
2. US1 → account API (test with minted token).
3. US2 → login screen + role-aware console (refresh-safe; non-admins fenced).
4. US3 → bootstrap admin + lockout safety.
5. Polish → retire old creds, docs, full green gates + quickstart.

---

## Notes

- `[P]` = different files, no dependency on an incomplete task.
- Every `/admin/users/*` and `/admin/auth/me` route is `require_admin`/`require_operator`; `/admin/auth/login`
  is the only unauthenticated `/admin` route.
- Never weaken an eval threshold to pass T029 — fix the cause (Constitution Development Workflow).
- Commit after each task or logical group; stop at any checkpoint to validate a story independently.
