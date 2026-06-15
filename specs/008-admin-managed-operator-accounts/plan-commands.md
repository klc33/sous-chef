# Phase 8 — Admin-Managed Operator Accounts (paste-ready SpecKit commands)

> Same format as [sous-chef-spec-plan.md](../../projectplanFolderForMd/sous-chef-spec-plan.md) §4: each block
> is **copy-paste ready** for its SpecKit step. Run them in order, review the generated artifacts, then
> `/speckit.implement` and pass the gates before moving on. → `specs/008-admin-managed-operator-accounts`.
>
> **Auth mechanism: JWT.** Login validates a username/password against a durable account store and issues a
> **signed JSON Web Token** (carrying the username + role + expiry). The dashboard persists that token across
> refreshes and presents it to the backend, which verifies the token and enforces the admin-vs-user role
> server-side. This **replaces** the old single shared `ADMIN_API_TOKEN` + single operator password hash.
>
> **Scope guard (must not break):** operator-console auth only. The cook widget, its passwordless
> `X-Profile-ID` identity, and the public `/chat` boundary are untouched; the wall, grounding, redaction, and
> red-team gates are unchanged. JWT is justified per Constitution P10 by the multi-user, role-based requirement.

**Objective:** replace the dashboard's single-operator login with named, admin-managed accounts authenticated
by JWT — login-only (no self-signup), an admin-only Users page, a bootstrapped first admin, and refresh-safe
sessions.
**Features:** durable operator-account store + Alembic migration; bcrypt password hashing; JWT issue/verify
seam (signing key from Vault); `/admin/auth/login` + role-aware `/admin/*` authorization; admin-only user CRUD
(create / list / deactivate / reactivate) with last-admin lockout protection; first-run admin bootstrap;
professional login screen + Users management page on the dashboard; retire `OPERATOR_USERNAME` + single hash +
static `ADMIN_API_TOKEN`.
**Effort:** ~1.5–2 days. **Dependencies:** Phase 1 (Vault, db, config, errors); Phase 4 (the dashboard +
`api/admin` + `admin_deps.py` this evolves).

---

### `/speckit.specify`  *(already executed — see [spec.md](./spec.md); included for regeneration only)*
```
/speckit.specify
Replace the Streamlit operator dashboard's single hardcoded-operator login with an admin-managed, multi-user
account system for the OPERATOR CONSOLE ONLY (not cook/end-user auth).
USER STORIES:
- As an admin, I sign in and, from a Users area, create / list / deactivate / reactivate operator accounts;
  no self-service sign-up exists anywhere.
- As a provisioned operator (non-admin), I sign in through a professional login screen and use the console,
  but I cannot see or reach the Users area.
- As anyone, a browser refresh keeps me signed in; a fresh deployment always has one bootstrapped admin; the
  last remaining active admin can never be deactivated.
FUNCTIONAL REQUIREMENTS:
- Login-only: no registration/sign-up path on the login screen or anywhere in the dashboard.
- Only an admin role can create accounts; each account is role admin|user and status active|disabled.
- Usernames are unique; empty/too-weak passwords are rejected; wrong username/password give ONE generic error
  (no account enumeration). Deactivated accounts cannot sign in.
- Credentials are stored durably; passwords only as irreversible hashes; user-management actions are audited
  with no secret/hash written to logs or traces.
- The previous single-operator credential ceases to grant access once accounts exist.
ACCEPTANCE:
- An admin creates a working account in <2 min; a non-admin reaches zero user-management functions; refresh
  never logs you out; a deactivated account is denied 100% of the time; administration can never be locked out.
- The cook widget, profile-ID identity, public chat, and the wall/grounding/redaction/red-team gates are
  unchanged and still green.
CONSTRAINTS: operator-console auth (allowed by the stack), NOT prohibited end-user auth; no torch; secrets in
Vault; reuse the existing datastore + dashboard auth surface; no new microservice.
```

### `/speckit.plan`
```
/speckit.plan
Implement admin-managed operator accounts with JWT auth, per the Constitution (monolith, simple, secrets in
Vault, repo-only DB access). Operator-console boundary only — the cook widget and public /chat are untouched.

DEPENDENCIES (uv, backend group only — keep the dashboard image lean):
  uv add --optional backend pyjwt        # issue/verify the signed login token (HS256)
  uv add --optional backend bcrypt       # one-way password hashing (matches the dashboard's existing bcrypt)
No torch; PyJWT + bcrypt are justified by the multi-user, role-based-auth requirement (P10).

DATA MODEL (app/models/operator_account.py + Alembic migration in alembic/versions):
  operator_accounts(id PK, username UNIQUE NOT NULL, display_name, role ENUM('admin','user') NOT NULL,
  is_active BOOL NOT NULL DEFAULT true, password_hash NOT NULL, created_by, created_at, updated_at).
  New migration only — no change to recipes/profiles/favorites/seen_history (cook side stays intact).

REPO (app/repo/operator_accounts.py — the ONLY DB access for accounts; parameterized/ORM):
  get_by_username, list_all, create, set_active(username, bool), count_active_admins, exists_any.

SERVICE (app/services/admin/operator_accounts.py — business rules, audience = operator):
  create_account (reject duplicate username + weak/empty password, hash via bcrypt), list_accounts,
  deactivate/reactivate (REFUSE deactivating the last active admin — count_active_admins guard),
  authenticate(username, password) -> account|None (constant-time; generic failure, no enumeration),
  bootstrap_admin() (if exists_any() is false, create one admin from the Vault bootstrap credential —
  idempotent, never a second admin), all emitting audit log lines with NO password/hash/secret.

AUTH SEAM (app/core/security.py or app/infra/auth.py):
  hash_password / verify_password (bcrypt); issue_token(account) -> JWT with claims {sub: username,
  role, exp}; decode_token(token) -> claims (raise on expired/invalid signature). Signing key
  JWT_SIGNING_KEY from Vault (never env/code/image).

API (app/api/admin/):
  auth.py  -> POST /admin/auth/login  (body: username+password; returns a JWT + role on success, generic
              401 on failure); optional GET /admin/auth/me (echo current claims).
  users.py -> POST /admin/users (create), GET /admin/users (list), POST /admin/users/{username}/deactivate,
              POST /admin/users/{username}/reactivate — ALL admin-role only.
  api/admin_deps.py: REPLACE the static shared-token check with JWT validation. require_operator(...) accepts
  any valid non-expired token for an active account; require_admin(...) additionally asserts role == 'admin'
  and is used by every /admin/users/* route. Login is the one unauthenticated /admin endpoint.

BOOTSTRAP: call bootstrap_admin() once at startup (app/main.py lifespan) so a fresh deployment always has a
way in; reads bootstrap username + initial password from Vault, then never re-seeds.

DASHBOARD (dashboard/auth.py + a new Users page):
  - require_login(): render a POLISHED, professional login screen; on submit POST credentials to
    /admin/auth/login, store the returned JWT in the signed session cookie (reuse the existing
    streamlit-authenticator cookie key from Vault) so a refresh re-hydrates and stays logged in (FR-013).
    The dashboard NO LONGER holds any password hash — credential checking moves to the backend.
  - admin_client(): attach the stored JWT as `Authorization: Bearer <jwt>` (replaces the static admin token).
  - require_admin(): gate the Users page; non-admins never see it (FR-011).
  - dashboard/pages/4_users.py: admin-only — create account form (username, initial password, role), a table
    of accounts (username/role/status), and deactivate/reactivate actions; NO sign-up entry point anywhere.

SECRETS (scripts/seed_vault.sh + VaultAdapter lookups + .env.example notes):
  ADD: JWT_SIGNING_KEY, BOOTSTRAP_ADMIN_USERNAME, BOOTSTRAP_ADMIN_PASSWORD (initial only). KEEP:
  DASHBOARD_COOKIE_KEY (cookie signing). RETIRE: OPERATOR_USERNAME, OPERATOR_PASSWORD_HASH, and the static
  ADMIN_API_TOKEN (per-user JWT replaces it). .env.example carries names only — never values.

SECURITY: bcrypt hashing; constant-time auth + single generic 401 (no enumeration, FR-012); short-lived JWT
with exp; role enforced server-side (not just hidden in the UI); last-admin lockout guard (FR-015); redaction
covers any auth log/trace so no credential leaks (P6); parameterized queries only.

MIGRATION/RETIREMENT: Alembic upgrade adds operator_accounts; bootstrap seeds the first admin; document that
on deploy the old single-operator credential stops granting access. No cook-side schema touched.

TESTING (tests/):
  unit: operator_accounts service (duplicate-username reject, weak-password reject, deactivate, last-admin
  guard refuses, bootstrap idempotency, generic-auth-failure); security (hash/verify roundtrip, JWT
  issue→decode, expired/invalid token rejected).
  integration: login → receive JWT → call an /admin route; non-admin token → 403 on /admin/users/*; deactivated
  account → login denied.
  regression: existing cook-facing tests (chat_flow, favorites, freshness, wall, redaction, red-team) stay
  GREEN and unchanged — proving the public side is unaffected (SC-007).

DOCS: SECURITY.md (JWT model, role boundary, bootstrap, retired shared token), RUNBOOK.md (seed the bootstrap
admin secrets; how an admin provisions/deactivates users), DECISIONS.md (why JWT + per-user roles over the
single shared token).
```

### `/speckit.tasks`
```
/speckit.tasks      # generate ordered tasks from the plan, then /speckit.implement,
                    # then: make lint && make test && make evals  (all green; wall/grounding/redaction/red-team unchanged)
```

---

### Suggested change-log entry (for [sous-chef-spec-plan.md](../../projectplanFolderForMd/sous-chef-spec-plan.md) §7, if adopted)

| # | Previous state | New state | Reason | Impacted artifacts |
|---|---|---|---|---|
| 13 | Dashboard = single hardcoded operator (one Vault hash) + static shared `ADMIN_API_TOKEN`; "no full end-user auth" | **Admin-managed operator accounts** with **JWT** auth: per-user login issues a signed token carrying role; admin-only Users CRUD; bootstrapped first admin; refresh-safe sessions; static admin token retired | Access must be grantable/revocable per person with an admin-vs-user split; this is operator-console auth (allowed by the stack), not the prohibited cook/end-user auth, so P10 holds | Phase 8 (this feature) · `api/admin` + `admin_deps` · dashboard auth · Architecture (operator boundary) |
