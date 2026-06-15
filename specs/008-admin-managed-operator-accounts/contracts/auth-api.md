# Contract — Operator Auth & User-Management API

All routes live under the existing operator surface (`app/api/admin/`). Bodies/responses are Pydantic
models in `app/schemas/operator.py`. `/admin/auth/login` is the **only** unauthenticated `/admin` route;
everything else requires a valid JWT (see [authz-model.md](authz-model.md)).

## POST `/admin/auth/login`  *(unauthenticated)*

Validate credentials and mint a JWT.

**Request**
```json
{ "username": "string", "password": "string" }
```

**200 OK**
```json
{ "access_token": "<jwt>", "token_type": "bearer", "role": "admin | user", "username": "string" }
```

**401 Unauthorized** — wrong password, unknown username, OR a disabled account. **One generic body** (no
enumeration, FR-012):
```json
{ "code": "auth_invalid_credentials", "detail": "Invalid username or password." }
```

Notes: constant-time verify (a dummy bcrypt compare runs when the username is unknown). No lockout counter
in v1.

## GET `/admin/auth/me`  *(require_operator)*

Echo the caller's identity from the verified token — used by the dashboard to decide whether to show the
Users page.

**200 OK**
```json
{ "username": "string", "role": "admin | user" }
```

## POST `/admin/users`  *(require_admin)*

Create an account. No self-service path exists; this is the only creation route and it is admin-gated.

**Request**
```json
{ "username": "string", "password": "string", "role": "admin | user", "display_name": "string|null" }
```

**201 Created** → `AccountView` (below).

**409 Conflict** — `{ "code": "user_exists", "detail": "Username already taken." }` (FR-009).
**422 Unprocessable** — empty/`<8`-char password or invalid role (FR-010).

## GET `/admin/users`  *(require_admin)*

List all accounts (active and disabled), newest first.

**200 OK** → `AccountView[]`

```jsonc
// AccountView
{ "username": "string", "display_name": "string", "role": "admin | user",
  "is_active": true, "created_by": "string|null", "created_at": "iso-8601" }
```
> `password_hash` is **never** serialized.

## POST `/admin/users/{username}/deactivate`  *(require_admin)*

Disable an account.

**200 OK** → updated `AccountView` (`is_active: false`).
**409 Conflict** — `{ "code": "last_admin", "detail": "Cannot deactivate the last active admin." }`
(FR-015 / SC-006).
**404 Not Found** — unknown username.

## POST `/admin/users/{username}/reactivate`  *(require_admin)*

Re-enable a disabled account → **200 OK** updated `AccountView` (`is_active: true`). **404** if unknown.

## POST `/admin/users/{username}/reset-password`  *(require_admin)*

Set a new password for an account (the admin-only recovery path; there is no self-service change in v1 —
FR-008a).

**Request**
```json
{ "password": "string" }
```

**200 OK** → updated `AccountView` (no hash/token in the body).
**422 Unprocessable** — empty or `<8`-char password (same rule as creation, FR-010).
**404 Not Found** — unknown username.

Notes: the new hash replaces `password_hash` and bumps `updated_at`; existing tokens for that account stay
valid until they expire (revocation is deactivation-driven, research D2) — reset rotates the credential, not
the live session.

## Error envelope

All errors use the project's existing `AppError` → `{ "code", "detail" }` shape (see `app/core/errors.py`),
so the dashboard and tests parse one consistent format. No password, hash, or token value ever appears in an
error body, log line, or trace span (FR-018, redaction).
