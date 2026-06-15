# Contract — Admin Cook-Management API

Cook accounts are provisioned/managed by an **operator admin (008)** from the dashboard. All routes live
under `app/api/admin/cooks.py` and are gated by 008's **`require_admin`** (an operator with the admin role).
There is no self-service cook creation anywhere (FR-002/FR-003).

## POST `/admin/cooks`  *(require_admin)*

Create a cook account.

**Request**
```json
{ "username": "string", "password": "string", "display_name": "string|null" }
```
**201 Created** → `CookView`.
**409 Conflict** — `{ "code": "cook_exists", "detail": "Username already taken." }` (FR-006).
**422 Unprocessable** — empty/`<8`-char password (FR-007/FR-010).

## GET `/admin/cooks`  *(require_admin)*

List all cook accounts, newest first.

**200 OK** → `CookView[]`
```jsonc
// CookView
{ "username": "string", "display_name": "string", "is_active": true,
  "created_by": "string|null", "created_at": "iso-8601" }
```
> `password_hash` and `id` are never serialized; `username` is the handle the admin works with.

## POST `/admin/cooks/{username}/deactivate`  *(require_admin)*

Disable a cook account → **200 OK** `CookView` (`is_active:false`); the cook can no longer sign in and loses
access on the next request (FR-009). **404** if unknown.

## POST `/admin/cooks/{username}/reactivate`  *(require_admin)*

Re-enable → **200 OK** `CookView` (`is_active:true`). **404** if unknown.

## POST `/admin/cooks/{username}/reset-password`  *(require_admin)*

Set a new password (admin-only recovery; no self-service change in v1 — FR-010).

**Request** `{ "password": "string" }`
**200 OK** → `CookView`. **422** empty/`<8`-char. **404** unknown.
> Rotates the credential; existing cook sessions remain valid until they expire (revocation is
> deactivation-driven, [gating-model.md](gating-model.md)).

## Notes

- No last-admin guard here — cook accounts have no admin role; that protection is 008's concern.
- All actions audit-logged (actor = the operator admin) with no secret/hash in logs or traces (FR-016).
- The dashboard page `dashboard/pages/5_cooks.py` calls these via the existing `admin_client()` (008 operator
  token); it exposes **no** sign-up control.
