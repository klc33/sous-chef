# Quickstart — Verify Admin-Managed Operator Accounts (JWT)

How to prove the feature end-to-end after `/speckit-implement`. References the contracts
([auth-api.md](contracts/auth-api.md), [authz-model.md](contracts/authz-model.md),
[secrets-keyspace.md](contracts/secrets-keyspace.md)) rather than repeating shapes.

## Prerequisites

- `make up` brings up the stack (backend + Postgres + Vault [+ Redis optional] + Phoenix).
- `make seed` has written the new Vault keys (`JWT_SIGNING_KEY`, `BOOTSTRAP_ADMIN_PASSWORD`) and kept
  `DASHBOARD_COOKIE_KEY`; non-secret `BOOTSTRAP_ADMIN_USERNAME` defaults to `admin`.
- Alembic is at head (`0004_operator_accounts` applied).

## 1. Bootstrap admin exists on a fresh DB (FR-014)

```sh
# On first boot with an empty accounts table, startup seeds one admin.
curl -s localhost:8000/admin/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<BOOTSTRAP_ADMIN_PASSWORD>"}'
# → 200 { access_token, token_type:"bearer", role:"admin", username:"admin" }
```
Restart the backend and confirm **no second admin** is created (table still has one admin) — bootstrap is
idempotent.

## 2. Admin creates a user, lists, and the user logs in (US1 + US2)

```sh
TOKEN=<access_token from step 1>
# create a non-admin user
curl -s localhost:8000/admin/users -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"username":"casey","password":"hunter2pw","role":"user"}'        # → 201 AccountView
# list shows both accounts
curl -s localhost:8000/admin/users -H "Authorization: Bearer $TOKEN"     # → 200 [admin, casey]
# the new user can sign in on first try
curl -s localhost:8000/admin/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"casey","password":"hunter2pw"}'                       # → 200 role:"user"
```
**Expected**: SC-001 (working account in under 2 min); duplicate username → 409; empty/short password → 422.

## 3. Role split is enforced server-side (FR-011 / SC-005)

```sh
USER_TOKEN=<casey's access_token>
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/admin/users -H "Authorization: Bearer $USER_TOKEN"
# → 403  (admin_forbidden) — a non-admin cannot reach user management even with a valid token
```

## 4. Login-only & no enumeration (FR-003 / FR-012)

- There is **no** registration endpoint and **no** sign-up control in the dashboard (visual check on the
  login screen + a search of `dashboard/` for any "register/sign up" affordance → none).
- Wrong password and unknown username return the **same** generic 401 body:
```sh
curl -s localhost:8000/admin/auth/login -d '{"username":"admin","password":"wrong"}' -H 'Content-Type: application/json'
curl -s localhost:8000/admin/auth/login -d '{"username":"ghost","password":"wrong"}' -H 'Content-Type: application/json'
# → both: 401 { code:"auth_invalid_credentials", detail:"Invalid username or password." }
```

## 5. Deactivation denies access (FR-008 / SC-004) and last-admin is protected (FR-015 / SC-006)

```sh
# deactivate casey, then her login is denied
curl -s -X POST localhost:8000/admin/users/casey/deactivate -H "Authorization: Bearer $TOKEN"   # → 200 is_active:false
curl -s localhost:8000/admin/auth/login -d '{"username":"casey","password":"hunter2pw"}' -H 'Content-Type: application/json'
# → 401 (deactivated account cannot sign in; an existing token is also rejected on next call)

# the last active admin cannot be deactivated
curl -s -X POST localhost:8000/admin/users/admin/deactivate -H "Authorization: Bearer $TOKEN"
# → 409 { code:"last_admin", ... }
```

## 5b. Admin resets a password (FR-008a)

```sh
# admin sets a new password for casey (after reactivating her); the old one no longer works
curl -s -X POST localhost:8000/admin/users/casey/reset-password -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"password":"newpassw0rd"}'        # → 200 AccountView
curl -s localhost:8000/admin/auth/login -d '{"username":"casey","password":"newpassw0rd"}' -H 'Content-Type: application/json'
# → 200  (new password works); the old password → 401; a <8-char reset → 422. No self-service change exists.
```

## 6. Dashboard: professional login + refresh-safe + admin-only Users page (FR-002 / FR-013 / FR-011)

1. Open the Streamlit dashboard → a **polished login screen** (no sign-up link).
2. Sign in as `admin`; open the **Users** page; create/deactivate accounts there.
3. **Refresh the browser** → you stay signed in (cookie-persisted JWT).
4. Sign in as `casey` (after reactivating) → the **Users page is absent / unreachable**; the rest of the
   console works.

## 7. Nothing cook-facing broke (FR-019 / SC-007)

```sh
make lint && make test && make evals
```
**Expected**: all green, including the unchanged cook suites (chat_flow, favorites, freshness, wall) and the
safety gates (red-team, redaction). The public `/chat` + `X-Profile-ID` path is unmodified.

## Done when

Steps 1–7 pass and `make lint && make test && make evals` is green (Constitution Development Workflow gate).
