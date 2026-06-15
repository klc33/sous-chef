# Implementation Plan: Admin-Managed Cook Accounts (JWT)

**Branch**: `009-admin-managed-cook-accounts` | **Date**: 2026-06-15 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/009-admin-managed-cook-accounts/spec.md`

## Summary

Replace the cook's **passwordless `X-Profile-ID`** identity with a **named, admin-provisioned cook account**,
gate the app behind cook login, and make the cook's **diet/allergy profile, favorites, and seen-history
account-owned**. A cook signs in on the React widget (professional screen, **no self-signup**) and receives
a signed **cook-session JWT**; the backend validates it and threads the **cook account id as the owner key**
exactly where `X-Profile-ID` was used today — so `profiles`/`favorites`/`seen_history` keep working with a
near-mechanical re-key rather than a rewrite. An **operator/admin (feature 008)** creates / lists /
deactivates / reactivates / resets cook accounts from the **dashboard**. A **seeded demo/eval cook account**
lets CI and the live demo authenticate (no bypass), keeping the safety gates exercised on the authenticated
path.

**Separate identity domain (FR-013).** Cook accounts are their own table (`cook_accounts`) and their own
login surface (the widget) with their **own signing key** and a `typ:"cook"` token claim, so an operator
(008) token can never be replayed as a cook session and vice-versa. The two never share a credential store.

**Builds on 008 (implementation order).** This feature **reuses 008's `app/core/security.py`** (bcrypt +
JWT primitives) and the operator `require_admin` dependency + dashboard shell. **008 must be implemented
first**; 009 extends those seams — it does not duplicate them.

Delta from today:

- **Identity**: `X-Profile-ID` header → cook-session JWT; `require_profile_id` → `require_cook` returning the
  cook account id (a string) as the owner key.
- **Gating**: every cook endpoint (`/chat`, `/recipes*`, `/favorites*`, `/profile`) requires a valid cook
  session; anonymous and legacy-`X-Profile-ID` requests are refused. `/health` stays open.
- **Ownership**: `profiles`/`favorites`/`seen_history` re-keyed to the cook account; existing anonymous rows
  are dropped (fresh start, FR-015).
- **Admin**: new admin-only cook-management API + dashboard page (gated by 008 `require_admin`).
- **Demo/eval**: a seeded demo cook account; the eval runner + smoke test log in and send a Bearer token.

## Technical Context

**Language/Version**: Python 3.12 (backend, dashboard); Node 20 + React/JSX (widget, plain JS, no TS).

**Primary Dependencies**: FastAPI + Pydantic; SQLAlchemy + Alembic; PostgreSQL (psycopg); hvac (Vault);
**reuses PyJWT + bcrypt added in 008** (no new backend dependency); slowapi (rate limiting, re-keyed to the
cook account); React + Vite (widget login). **No new Python or JS runtime dependency**; no `torch`.

**Storage**: PostgreSQL — one new table `cook_accounts`; `profiles`/`favorites`/`seen_history` re-keyed to
the cook account (Alembic `0005`). No change to recipes/embeddings or to 008's `operator_accounts`.

**Testing**: pytest — unit (cook-account service rules), integration (cook login → JWT → gated `/chat`,
data-isolation between two cooks, deactivated-cook denied, admin cook-management authz), and a **regression
guard** that the wall/grounding/redaction/red-team suites stay green now that they authenticate as the
seeded demo cook (SC-008).

**Target Platform**: existing backend + widget + dashboard services (local compose + Railway). No new
service/datastore.

**Project Type**: web monolith + React widget + Streamlit dashboard. This feature gates the widget/public
API and adds cook-account management to the operator dashboard.

**Performance Goals**: not a perf feature. Login = one bcrypt verify + one JWT sign. Gating adds one token
decode + one indexed account lookup per cook request (the request already hits the DB).

**Constraints**: cook session signing key + demo-cook password only in Vault; passwords one-way hashed in the
DB; **cook session JWT is authoritative** (8h, mirrors 008) and is the only identity — never a body/header id;
constant-time auth + generic 401 (no enumeration); separate signing key + `typ:"cook"` claim isolate the two
domains; redaction covers any auth log/span; images lean, no torch. Total gating — no anonymous mode.

**Scale/Scope**: a modest set of cook accounts (internal/demo scale). Scope = one table + re-key migration +
cook auth/login + gating + admin cook-management (API + dashboard page) + widget login + seeded demo cook +
docs. 008 and all safety paths are out of scope to change.

## Constitution Check

*GATE: must pass before Phase 0 and re-checked after Phase 1 design.* (Constitution **2.0.0**.)

| Principle | Gate | Verdict |
|-----------|------|---------|
| I. Simplicity | Monolith; no new service/datastore; one agent untouched. | **PASS** — one table, a re-key migration, and routes inside existing surfaces; reuses 008's security seam rather than adding one. |
| II. Build only what's required | Every task traces to an FR. | **PASS** — maps to FR-001..FR-020; self-service password change + SSO/MFA explicitly deferred. |
| III. Separation of concerns | `api → services → repo → infra`; repo-only DB; user vs admin split. | **PASS** — `repo/cook_accounts.py` is the only DB toucher for accounts; cook-management logic in `services/admin/`; cook auth in `api/user` + `core/security`; the re-key keeps the owner key flowing through unchanged repos. |
| IV. Testability | Critical + safety behaviors tested/gated; adapters mockable. | **PASS** — data isolation, gating, deactivation, admin authz unit/integration tested; the red-team/redaction gates now run as the seeded cook and must stay green. |
| V. Reproducibility | Fresh clone runs identically; Alembic; pinned deps. | **PASS** — one migration; seeded demo cook makes a fresh DB demo-ready; no new deps. |
| VI. Security & privacy by default | Vault secrets; redaction; parameterized queries; safe-by-default. | **PASS, strengthened** — the formerly-open chat is now authenticated; per-cook accountability; domain-isolated tokens; generic auth errors; rate limiting retained (re-keyed to the account). |
| VII. Maintainability | Readable, consistent, documented. | **PASS** — mirrors 008's model/repo/service/api patterns; SECURITY/RUNBOOK/DECISIONS + the CLAUDE.md identity convention updated. |
| VIII. Documentation-first | Spec before code; docs in sync. | **PASS** — spec + clarify + this plan precede code; CLAUDE.md "passwordless profile-ID" convention is corrected here. |
| IX. Spec-driven | specify → plan → tasks → implement, committed. | **PASS** — this is that flow. |
| X. No unnecessary tech | Approved stack; **end-user auth now allowed (2.0.0)**. | **PASS** — no new dependency (reuses 008's PyJWT/bcrypt); admin-provisioned end-user auth + total gating are exactly what constitution 2.0.0 + the *Account & Authentication Model* section permit; self-service signup remains prohibited and is not added. |
| Safety invariants | Wall, grounding, hosted-only inference, lean classifier. | **PASS** — the wall/grounding/redaction run unchanged on the authenticated path; recipes still leave only via `recipe_view`; no model weights, no torch. |

**Result: PASS.** No new technology and no principle conflict under 2.0.0. The one thing to record is the
**implementation dependency on 008** (the shared `core/security` seam + `require_admin` + dashboard shell) —
captured in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/009-admin-managed-cook-accounts/
├── spec.md              # WHAT & WHY (+ Clarifications)
├── plan.md              # This file
├── research.md          # Phase 0: identity re-key, domain isolation, gating, token storage, demo cook
├── data-model.md        # Phase 1: cook_accounts schema + the profiles/favorites/seen_history re-key
├── quickstart.md        # Phase 1: verify (login → own data → isolation → gating → admin mgmt → gates green)
├── contracts/           # Phase 1: the cook-auth, gating, admin-cook, and secrets contracts
│   ├── cook-auth-api.md   #   POST /auth/login, GET /auth/me; the cook-session token
│   ├── gating-model.md    #   require_cook; which endpoints gate; legacy/anon handling
│   ├── admin-cooks-api.md #   /admin/cooks CRUD (operator-admin gated)
│   └── secrets-keyspace.md# Vault keys added for cook sessions + the demo cook
└── checklists/
    └── requirements.md  # Spec quality checklist (from /speckit-specify)
```

### Source Code (repository root) — files this feature adds or changes

```text
sous-chef/
├── app/
│   ├── config.py                        # CHANGE: add VAULT_KEY_COOK_SESSION_KEY + VAULT_KEY_DEMO_COOK_PASSWORD;
│   │                                    #   non-secret demo_cook_username, cook_jwt_ttl_minutes (480)
│   ├── main.py                          # CHANGE: seed the demo cook on startup (idempotent); mount cook auth router
│   ├── core/
│   │   └── security.py                  # REUSE (from 008) + extend: issue/verify a `typ:"cook"` token with the cook signing key
│   ├── models/
│   │   └── cook_account.py              # ADD: CookAccount ORM model
│   ├── repo/
│   │   ├── cook_accounts.py             # ADD: ONLY DB access for cook accounts (get/list/create/set_active/set_password/exists_any)
│   │   └── profiles.py                  # CHANGE: owner key now references cook_accounts (FK); same get/upsert/ensure_exists shape
│   ├── services/
│   │   ├── user/
│   │   │   └── cook_auth.py             # ADD: authenticate(username,password) + issue cook session; bootstrap_demo_cook()
│   │   └── admin/
│   │       └── cook_accounts.py         # ADD: admin create/list/deactivate/reactivate/reset (reuses validation rules)
│   ├── schemas/
│   │   ├── cook_auth.py                 # ADD: CookLoginRequest, CookTokenResponse, CookView
│   │   └── operator.py                  # (008) reused patterns
│   └── api/
│       ├── deps.py                      # CHANGE: replace require_profile_id with require_cook (validate cook JWT → owner id)
│       ├── user/
│       │   ├── __init__.py              # CHANGE: register the cook auth router; keep rate limiter (re-keyed)
│       │   ├── auth.py                  # ADD: POST /auth/login, GET /auth/me (login is the only open cook route)
│       │   ├── chat.py                  # CHANGE: ProfileId→CookId dep; rate-limit key = cook account id
│       │   ├── recipes.py               # CHANGE: ProfileId→CookId dep
│       │   ├── favorites.py             # CHANGE: ProfileId→CookId dep
│       │   └── profile.py               # CHANGE: ProfileId→CookId dep
│       └── admin/
│           ├── __init__.py              # CHANGE: register the cooks router
│           └── cooks.py                 # ADD: /admin/cooks CRUD — all require_admin (008)
├── alembic/versions/
│   └── 0005_cook_accounts.py            # ADD: create cook_accounts; re-key profiles→cook_accounts FK; clear old anon rows
├── dashboard/
│   └── pages/
│       └── 5_cooks.py                   # ADD: admin-only Cook Accounts page (create/list/deactivate/reactivate/reset)
├── widget/src/
│   ├── lib/
│   │   ├── profile.js                   # REMOVE/REPLACE: passwordless id retired
│   │   └── session.js                   # ADD: store/clear the cook session token (localStorage); expiry-aware
│   ├── api/client.js                    # CHANGE: send Authorization: Bearer <cook jwt> (drop X-Profile-ID); 401 → login
│   ├── components/
│   │   └── Login.jsx                    # ADD: professional cook login screen (no signup)
│   └── App.jsx                          # CHANGE: gate the app behind login; sign-out control
├── scripts/
│   └── seed_vault.sh                    # CHANGE: seed COOK_SESSION_KEY + DEMO_COOK_PASSWORD
├── evals/                               # CHANGE: eval runner + smoke test log in as the demo cook, send Bearer token
├── .env.example                         # CHANGE: note demo_cook_username, cook_jwt_ttl_minutes (non-secret)
├── tests/
│   ├── unit/test_cook_accounts.py       # ADD: dedupe/weak-pw/deactivate/reset/generic-failure/demo-bootstrap
│   └── integration/
│       ├── test_cook_auth_gating.py     # ADD: login→JWT→/chat ok; anon/legacy→401; deactivated→denied
│       └── test_cook_data_isolation.py  # ADD: two cooks never see each other's favorites/profile
└── docs/
    ├── SECURITY.md                      # CHANGE: cook auth, domain isolation, gating, demo cook
    ├── RUNBOOK.md                       # CHANGE: seed cook-session key + demo cook; admin manages cooks; demo login
    └── DECISIONS.md                     # CHANGE: end-user accounts + total gating over passwordless profile-ID
```

**Structure Decision**: No new top-level structure. Cook auth slots into the existing `api/user` + `core/
security`; cook-account *management* into `api/admin` + `services/admin` (the operator audience); accounts get
their own `repo`/`model`. The widget gains a login gate. The re-key keeps the owner-key contract identical
downstream, so `recipes`/`favorites`/`seen_history`/the wall need no logic change — only their identity
source changes.

## Complexity Tracking

| Item | Why Needed | Simpler Alternative Rejected Because |
|------|------------|-------------------------------------|
| **Depends on 008** (`core/security`, `require_admin`, dashboard shell) | Cook auth reuses the same bcrypt/JWT primitives and the admin who manages cooks is an 008 admin — duplicating either would be worse. | Re-implementing hashing/JWT for cooks = two copies to keep correct; a standalone admin for cooks = a second admin system. 008-first is the lean path. |
| **Separate cook signing key + `typ:"cook"` claim** | FR-013 demands isolated domains; without isolation an operator token could be replayed as a cook session. | A single shared key/claim blurs the domains and widens blast radius if either leaks. |
| **Re-key migration drops existing anonymous rows** | FR-015 (fresh start); anonymous `profile_id` UUIDs cannot satisfy the new `cook_accounts` FK. | Migrating ownerless rows would invent owners; keeping them orphaned breaks the FK. Dropping is the honest choice. |
