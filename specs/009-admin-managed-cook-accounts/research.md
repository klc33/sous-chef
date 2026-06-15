# Phase 0 — Research & Decisions: Admin-Managed Cook Accounts (JWT)

Scoped to the **cook** identity domain. Reuses feature 008's `core/security` primitives; 008 ships first.

## D1 — Identity re-key: cook account id becomes the owner key

- **Decision**: `require_profile_id` (reads `X-Profile-ID`) is replaced by `require_cook`, which validates the
  cook-session JWT and returns the **cook account id as a string** — the same shape the codebase already
  threads everywhere as `profile_id`. `profiles`/`favorites`/`seen_history` keep keying on that value.
- **Rationale**: The whole app already funnels the owner key through one dependency and one repo per table.
  Swapping the *source* of that key (authenticated account instead of a header) is a near-mechanical change
  and leaves `recipes`/`favorites`/`seen_history`/the wall logic untouched (Constitution I/III).
- **Alternatives**: a brand-new `cook_account_id` column threaded through every service/repo (large, churny);
  rejected — the existing owner-key seam already gives us the indirection.

## D2 — Domain isolation from operator accounts (FR-013)

- **Decision**: cook sessions use a **separate signing key** (`COOK_SESSION_KEY`, Vault) **and** a
  `typ:"cook"` claim. `require_cook` accepts only `typ:"cook"` tokens verified with the cook key; the 008
  operator dependencies accept only operator tokens. Separate tables, separate login surfaces.
- **Rationale**: Prevents an operator (dashboard) token from ever being replayed as a cook session or vice
  versa, and limits blast radius if one key leaks — exactly the "two separate systems" the user chose.
- **Alternatives**: one shared key + an audience claim (smaller blast-radius isolation; one leak hits both);
  rejected for true domain separation.

## D3 — Gating scope: which endpoints require a cook session

- **Decision**: `require_cook` gates **all** cook endpoints — `/chat`, `/recipes` + `/recipes/{id}`,
  `/favorites*`, `/profile`. `POST /auth/login` is the only open cook route; `/health` stays open;
  `/admin/*` keeps its 008 operator boundary. A request with no token, an expired token, or the legacy
  `X-Profile-ID` header is treated as unauthenticated → **401** (no anonymous mode).
- **Rationale**: FR-001/FR-012 — total gating, identity only from the authenticated session.
- **Alternatives**: leave `/recipes` browseable anonymously (partial gating); rejected — the spec is explicit
  that there is no anonymous use.

## D4 — Cook session token: lifetime & revocation

- **Decision**: cook JWT carries `{sub: cook_account_id, typ:"cook", iat, exp}` with **8h `exp`** (config
  `cook_jwt_ttl_minutes`, mirrors 008). Revocation is **deactivation-driven**: `require_cook` re-checks the
  account exists and `is_active` on every request, so a deactivated cook is denied at the next call.
- **Rationale**: same stateless-token + per-request account re-check pattern proven in 008 (research D2
  there); satisfies FR-009 without a denylist.
- **Alternatives**: server-side session store / token denylist — extra moving parts; rejected per
  Constitution I.

## D5 — Token storage in the browser (SPA)

- **Decision**: store the cook JWT in **`localStorage`** (a new `lib/session.js`), send it as
  `Authorization: Bearer <jwt>`; on a `401` the client clears it and shows the login screen. Refresh
  re-hydrates from `localStorage` (FR-011).
- **Rationale**: mirrors the existing `profile.js` localStorage pattern and keeps the backend **header-based**
  (CORS `allow_credentials=False` is unchanged — no cookie/CORS rework). Simplest refresh-safe option.
- **Trade-off / alternative**: an httpOnly cookie resists XSS token theft but requires CORS credentialed mode
  + CSRF handling and a cookie/issuer change on the backend. Deferred; `localStorage` is acceptable at this
  scope and is the lighter change. (Recorded so a future hardening pass can revisit.)

## D6 — Admin cook-management lives on the operator dashboard

- **Decision**: cook accounts are created/listed/deactivated/reactivated/reset via **`/admin/cooks`**
  (gated by 008 `require_admin`) and a dashboard page `5_cooks.py`. The acting admin is an 008 operator with
  the admin role.
- **Rationale**: FR-003 + the user's choice ("admin manages from the dashboard"); reuses the 008 admin
  boundary rather than inventing a cook-admin.
- **Alternatives**: an admin panel inside the widget (the user rejected this earlier).

## D7 — Seeded demo/eval cook (FR-020) — how gated CI/demo authenticate

- **Decision**: a **demo cook account** is seeded at startup (idempotent, like 008's bootstrap admin) from
  `DEMO_COOK_USERNAME` (config) + `DEMO_COOK_PASSWORD` (Vault). The **eval runner and the CI smoke test log
  in** as this cook and attach the Bearer token to every `/chat` call; the **live demo** signs in as it too.
- **Rationale**: keeps the login gate real (no test bypass to maintain or accidentally ship) and keeps the
  red-team/redaction/RAG gates exercising the authenticated path (SC-008).
- **Alternatives**: a test-only auth bypass flag (a second code path + a foot-gun if it ships); rejected.

## D8 — Re-key migration & existing anonymous data (FR-015)

- **Decision**: migration `0005` creates `cook_accounts`, then **clears existing `profiles` / `favorites` /
  `seen_history` rows** (they are ownerless anonymous data) and adds a FK from the cook owner key to
  `cook_accounts`. Account-owned data starts fresh. `downgrade()` drops the FK + table (data is not restored).
- **Rationale**: anonymous `profile_id` UUIDs can't satisfy the new FK and have no real owner (FR-015);
  clearing is the honest, reproducible choice.
- **Alternatives**: migrate anon rows to a placeholder account (invents ownership); leave orphaned (breaks the
  FK). Both rejected.

## D9 — Password rules, generic failure, reset (mirror 008)

- **Decision**: `< 8`-char/empty passwords rejected on create and admin reset; unknown-username and
  wrong-password yield one generic 401 (constant-time, dummy verify); admin-only reset, no self-service
  change. Cook accounts have **no role** (all cooks).
- **Rationale**: consistency with 008 + spec FR-007/FR-008/FR-010; one mental model for both domains.

## D10 — Rate limiting re-keyed to the account

- **Decision**: the `/chat` slowapi limiter keys on the **cook account id** (from the token) instead of the
  `X-Profile-ID` header; same `30/minute` budget per cook.
- **Rationale**: preserves the per-cook budget against shared hosted APIs now that identity is the account.

## Open clarifications

None blocking. Deferred-by-design (spec Assumptions): self-service password change, SSO/MFA/OAuth, multi-tier
cook roles, httpOnly-cookie token storage (D5 trade-off), migrating legacy anonymous data.
