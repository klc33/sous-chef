# Contract — Cook Gating Model (require_cook)

Replaces `require_profile_id` in `app/api/deps.py`. The cook's identity now comes **only** from the verified
cook-session JWT — never a header or body id.

## Token

- **Format**: JWT, **HS256**, signed with **`COOK_SESSION_KEY`** (Vault) — distinct from 008's operator key.
- **Claims**: `sub` = `cook_accounts.id`, `typ` = `"cook"`, `iat`, `exp` (`iat + cook_jwt_ttl_minutes`, 8h).
- **Transport**: `Authorization: Bearer <cook-jwt>` on every cook request (the widget's `client.js` attaches
  it).

## `require_cook(request, authorization)` → owner id (str)

Gates **all** cook endpoints. Algorithm:

1. Missing/malformed `Authorization: Bearer …` → **401** `cook_unauthorized`.
2. `decode_token()` with the **cook** key; reject if invalid signature, expired, or `typ != "cook"` (an
   operator/008 token is rejected here) → **401** `cook_unauthorized`.
3. Load `sub` via `repo.cook_accounts.get`. Missing **or** `is_active == false` → **401** `cook_unauthorized`
   (immediate revocation of a deactivated cook — research D4).
4. Return `cook_accounts.id` (string) — the **owner key** the routers/repos use exactly where `profile_id`
   was.

> A legacy `X-Profile-ID` header (or no auth) carries no valid Bearer token → step 1 → 401. There is no
> anonymous path (FR-001/FR-012).

## Gated vs open endpoints

| Endpoint | Guard |
|---|---|
| `POST /auth/login` | **open** (mint a token) |
| `GET /health` | **open** (liveness/smoke) |
| `POST /chat` | `require_cook` (+ rate limit keyed to the cook id) |
| `GET /recipes`, `GET /recipes/{id}` | `require_cook` |
| `POST/GET/DELETE /favorites*` | `require_cook` |
| `GET/PUT /profile` | `require_cook` |
| `/admin/*` (incl. `/admin/cooks`) | 008 operator boundary (`require_operator` / `require_admin`) — unchanged |

## Migration from `X-Profile-ID`

- **Before**: `require_profile_id` trusted a client-supplied header as the owner key (no auth).
- **After**: `require_cook` returns the owner key from a verified token; the header is ignored/retired.
- **Unchanged downstream**: `recipes`, `favorites`, `seen_history`, `profile`, and the wall receive the same
  owner-key string and behave identically — only its source changed (research D1).

## Rate limiting

The `/chat` slowapi limiter keys on the **cook account id** from the token (was the `X-Profile-ID` header);
same `30/minute` per-cook budget against the shared hosted APIs.
