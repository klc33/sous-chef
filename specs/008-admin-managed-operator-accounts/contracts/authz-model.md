# Contract — Authorization Model (JWT + role)

Replaces the static shared-token check in `app/api/admin_deps.py`. Two dependencies gate the operator
surface; the human boundary (login UI) is the dashboard, the machine boundary is the JWT.

## Token

- **Format**: JWT, **HS256**, signed with `JWT_SIGNING_KEY` (Vault).
- **Claims**: `sub` (username), `role` (`admin|user`), `iat`, `exp` (`iat + jwt_ttl_minutes`, default 8h).
- **Transport**: `Authorization: Bearer <jwt>` header on every `/admin/*` call (the dashboard's
  `admin_client()` attaches it).

## `require_operator(request, authorization)`

Gates **all existing operator routes** (`/admin/corpus`, `/admin/evals`, `/admin/metrics`) and
`/admin/auth/me`.

Algorithm:
1. Missing/malformed `Authorization: Bearer …` → **401** `admin_unauthorized`.
2. `decode_token()` — invalid signature or expired → **401** `admin_unauthorized`.
3. Load `sub` via `repo.operator_accounts.get_by_username`. Missing **or** `is_active == false` → **401**
   `admin_unauthorized` (immediate revocation of deactivated accounts — research D2).
4. Otherwise return the resolved account/claims (available to the route).

## `require_admin(...)`

Gates **every** `/admin/users/*` route. Runs `require_operator` first, then:
- `role != "admin"` → **403** `admin_forbidden` (role enforced server-side, not just hidden in the UI —
  FR-011 / SC-005).

## Failure codes (consistent with existing `admin_deps`)

| Situation | Status | `code` |
|---|---|---|
| No/!malformed bearer, bad signature, expired, account missing/disabled | 401 | `admin_unauthorized` |
| Valid token but role is `user` on an admin-only route | 403 | `admin_forbidden` |

## Migration from the static token

- **Before**: `require_operator` compared a presented bearer to the Vault `ADMIN_API_TOKEN` in constant
  time; any holder had full access; no identity, no role.
- **After**: the bearer is a per-user JWT; identity = `sub`, role enforced; `ADMIN_API_TOKEN` retired.
- **Unchanged**: the public cook widget still holds no `/admin` credential and cannot reach any `/admin`
  route (FR-019); the boundary stays header-only (never reads a token from body/query).

## Notes

- One DB lookup per admin request (indexed `username`) — the operator routes already touch the DB, so this
  adds no new round-trip class.
- No token denylist in v1; deactivation + short TTL cover revocation (research D2).
