# Quickstart — Verify Admin-Managed Cook Accounts (JWT)

How to prove the feature end-to-end after `/speckit-implement`. References the contracts
([cook-auth-api.md](contracts/cook-auth-api.md), [gating-model.md](contracts/gating-model.md),
[admin-cooks-api.md](contracts/admin-cooks-api.md), [secrets-keyspace.md](contracts/secrets-keyspace.md))
rather than repeating shapes. **Prereq: feature 008 is implemented** (cook auth reuses its `core/security`
and the admin who manages cooks is an 008 admin).

## Prerequisites

- `make up` brings up the stack; Alembic at head (`0005_cook_accounts` applied).
- `make seed` wrote `COOK_SESSION_KEY` + `DEMO_COOK_PASSWORD`; `DEMO_COOK_USERNAME` defaults to `demo`.
- An 008 admin exists (008 bootstrap) and you have an operator admin token for the dashboard calls.

## 1. The app is gated — no anonymous access (FR-001/FR-012)

```sh
# No token (and the legacy header) are both rejected:
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/chat -X POST -H 'Content-Type: application/json' -d '{"message":"hi"}'
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/chat -X POST -H 'X-Profile-ID: anything' -H 'Content-Type: application/json' -d '{"message":"hi"}'
# → both 401 (cook_unauthorized).  GET /health stays 200.
```

## 2. Admin provisions a cook from the dashboard API (US2)

```sh
ATOKEN=<operator admin JWT from 008 login>
curl -s localhost:8000/admin/cooks -H "Authorization: Bearer $ATOKEN" -H 'Content-Type: application/json' \
  -d '{"username":"maya","password":"thai-night-1"}'        # → 201 CookView
curl -s localhost:8000/admin/cooks -H "Authorization: Bearer $ATOKEN"   # → 200 [demo, maya]
```
Duplicate username → 409; empty/short password → 422; a non-admin operator token → 403.

## 3. A cook signs in and owns their data (US1, SC-001/SC-004)

```sh
TOKEN=$(curl -s localhost:8000/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"maya","password":"thai-night-1"}' | jq -r .access_token)   # 200, cook JWT
# set profile + save a favorite (identity comes from the token, not a header):
curl -s -X PUT localhost:8000/profile -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"diet":"vegetarian","allergies":["tree_nuts"],"default_servings":2}'
# ... save a favorite via the favorites endpoint ...
# sign in again (fresh token / another browser) -> the profile + favorite are still there.
```
**Expected**: profile + favorites persist across sessions/devices (account-owned).

## 4. Data isolation between cooks (SC-004)

```sh
# Create a second cook, sign in, and confirm they see none of maya's favorites/profile.
```
**Expected**: each cook sees only their own favorites/profile/seen-history.

## 5. Deactivation denies access (FR-009 / SC-006)

```sh
curl -s -X POST localhost:8000/admin/cooks/maya/deactivate -H "Authorization: Bearer $ATOKEN"   # 200 is_active:false
curl -s localhost:8000/auth/login -d '{"username":"maya","password":"thai-night-1"}' -H 'Content-Type: application/json'
# → 401; and maya's existing token is rejected on her next /chat call.
```

## 6. Widget: professional login, refresh-safe, no signup (FR-002/FR-011)

1. Open the widget → a professional login screen, **no sign-up control**.
2. Sign in as `maya` → the app loads; her diet/allergies + favorites are present.
3. **Refresh** → still signed in (token re-hydrated from storage).
4. Sign out → back to the login screen.

## 7. The wall still holds per authenticated cook (FR-017 / SC-008)

Sign in as a tree-nut-allergic cook and browse/plan → **no** nut recipe is ever shown (the wall, now keyed to
the account).

## 8. CI / demo authenticate as the seeded cook; gates stay green (FR-020 / SC-008)

```sh
make lint && make test && make evals
```
**Expected**: all green. The eval runner + smoke test log in as the **demo** cook and send the Bearer token,
so the red-team + redaction + RAG gates run on the authenticated path. Feature 008 behavior is unchanged.

## Done when

Steps 1–8 pass and `make lint && make test && make evals` is green (Constitution Development Workflow gate),
with **008 unaffected**.
