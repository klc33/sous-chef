# Contract — Cook Auth API (widget-facing)

Cook login lives on the public `api/user` surface. `POST /auth/login` is the **only** unauthenticated cook
route; every other cook endpoint requires a valid cook session (see [gating-model.md](gating-model.md)).
Bodies/responses are Pydantic models in `app/schemas/cook_auth.py`.

## POST `/auth/login`  *(unauthenticated)*

Validate cook credentials and mint a cook-session JWT.

**Request**
```json
{ "username": "string", "password": "string" }
```

**200 OK**
```json
{ "access_token": "<cook-jwt>", "token_type": "bearer", "username": "string" }
```
Token claims: `{ sub: <cook_accounts.id>, typ: "cook", iat, exp }` (HS256, `COOK_SESSION_KEY`, 8h).

**401 Unauthorized** — wrong password, unknown username, OR a disabled account. One generic body (no
enumeration, FR-008):
```json
{ "code": "auth_invalid_credentials", "detail": "Invalid username or password." }
```

> No `role` is issued — cook accounts are role-less. No `/auth/register` exists anywhere (FR-002).

## GET `/auth/me`  *(require_cook)*

Echo the signed-in cook's identity (used by the widget to render the signed-in state).

**200 OK**
```json
{ "username": "string" }
```

## Error envelope

Uses the project's existing `AppError` → `{ "code", "detail" }` (see `app/core/errors.py`). No password,
hash, or token value appears in any error body, log line, or trace span (FR-016, redaction).
