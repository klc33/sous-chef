---
description: "Task list for 009-admin-managed-cook-accounts"
---

# Tasks: Admin-Managed Cook Accounts (JWT)

**Input**: Design documents from `specs/009-admin-managed-cook-accounts/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: INCLUDED — test-gated project (Constitution P4; SC-008 regression).

**🔢 IMPLEMENT ORDER — 008 FIRST, THEN 009.** This feature's Foundation **reuses** 008's
`app/core/security.py` (bcrypt + JWT) and the operator `require_admin` + dashboard shell, and the admin who
creates cook accounts IS an 008 operator admin. So **feature 008 MUST be implemented before this one** — do
not start 009's code until 008 exists, or Foundational tasks T004–T014 will reference modules that aren't
there yet. (Dependency is one-way: 008 knows nothing about 009.)

**Scope guard on EVERY task**: change only the cook identity domain + cook-account management. Do NOT change
008's `operator_accounts`/dashboard auth, the wall/grounding logic, or the recipe/embedding pipeline. The
wall, grounding, redaction, and red-team gates must stay green on the authenticated path (SC-008).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: different files, no dependency on an incomplete task
- **[Story]**: US1 / US2 / US3 (Setup / Foundational / Polish carry no story label)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: non-secret config + secret plumbing. No new dependencies (reuses 008's PyJWT/bcrypt).

- [X] T001 [P] Add config in `app/config.py`: constants `VAULT_KEY_COOK_SESSION_KEY = "COOK_SESSION_KEY"`, `VAULT_KEY_DEMO_COOK_PASSWORD = "DEMO_COOK_PASSWORD"`; settings `demo_cook_username` (default `"demo"`) and `cook_jwt_ttl_minutes` (default `480` = 8h)
- [X] T002 [P] Update `.env.example`: note `DEMO_COOK_USERNAME` and `COOK_JWT_TTL_MINUTES` (non-secret); never list secret values
- [X] T003 Update `scripts/seed_vault.sh`: seed `COOK_SESSION_KEY` + `DEMO_COOK_PASSWORD` (local placeholders + prod mandatory loop), per [contracts/secrets-keyspace.md](contracts/secrets-keyspace.md)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the cook identity backbone — table, re-key migration, security extension, repo, services, the
gating dependency, and the seeded demo cook. Per the plan these serve all stories.

**⚠️ CRITICAL**: No user story work begins until this phase is complete. Requires 008's `core/security`.

- [X] T004 Create `CookAccount` ORM model in `app/models/cook_account.py` per [data-model.md](data-model.md) (Text `id` PK, unique `username`, display_name, is_active default true, password_hash, created_by, timestamps; NO role)
- [X] T005 Register the model in `app/models/__init__.py` so Alembic autogenerate sees it (depends on T004)
- [X] T006 Create Alembic migration `alembic/versions/0005_cook_accounts.py` (down_revision `0004`): create `cook_accounts` + unique `username` index; **clear ownerless rows** (`DELETE FROM seen_history; favorites; profiles`) BEFORE adding the FK; add FK `profiles.profile_id → cook_accounts.id` (`ON DELETE CASCADE`); `downgrade()` drops FK + table. ⚠️ **IRREVERSIBLE DATA WIPE** (FR-015): this deletes all existing anonymous profile/favorites/seen-history data and `downgrade()` does NOT restore it — back up first on any env with real data (depends on T004)
- [X] T007 [P] Create Pydantic schemas in `app/schemas/cook_auth.py`: `CookLoginRequest`, `CookTokenResponse`, `CookView` (CookView never serializes `password_hash`/`id`) per [contracts/cook-auth-api.md](contracts/cook-auth-api.md)
- [X] T008 [P] Extend `app/core/security.py` (the 008 seam): `issue_cook_token(cook)` / `decode_cook_token(token)` — HS256 with Vault `COOK_SESSION_KEY`, `typ:"cook"` claim, exp = now + `cook_jwt_ttl_minutes`; reject non-`cook` tokens
- [X] T009 Create the repo `app/repo/cook_accounts.py` (ONLY DB access for cook accounts): `get(id)`, `get_by_username`, `list_all`, `create`, `set_active`, `set_password`, `exists_any` (depends on T004)
- [X] T010 Create `app/services/user/cook_auth.py`: `authenticate(username,password)` (constant-time, dummy-verify on miss, generic failure), `issue_session(cook)`, `bootstrap_demo_cook()` (idempotent — create `demo_cook_username` from Vault `DEMO_COOK_PASSWORD` if absent) (depends on T008, T009)
- [X] T011 Create `app/services/admin/cook_accounts.py`: `create_cook` (dup-username 409, `<8` password 422, bcrypt), `list_cooks`, `deactivate`/`reactivate`, `reset_password` (`<8` 422); audit log lines with NO password/hash (depends on T008, T009, T007)
- [X] T012 Rewrite the cook identity dep in `app/api/deps.py`: replace `require_profile_id` with `require_cook` — decode the cook token (reject missing/expired/`typ≠cook`/operator tokens → 401 `cook_unauthorized`), load `sub` via repo, reject missing/`is_active=false`, return the cook id (owner key) per [contracts/gating-model.md](contracts/gating-model.md) (depends on T008, T009)
- [X] T013 Wire `bootstrap_demo_cook()` into `app/main.py` startup lifespan (idempotent; after Vault load + DB ready) (depends on T010)
- [X] T014 [P] Unit tests `tests/unit/test_cook_accounts.py`: duplicate-username reject, weak/empty password reject (create AND reset), deactivate/reactivate, `reset_password` changes hash, `authenticate` generic failure (unknown vs wrong), `bootstrap_demo_cook` idempotency; cook-token issue→decode + `typ`/key isolation (an operator token is rejected by `decode_cook_token`) (depends on T010, T011, T008)
- [X] T014a [P] Unit test in `tests/unit/test_cook_accounts.py` for FR-016 audit: create/deactivate/reset emit an audit log line recording the action + actor (the operator admin), and assert the captured log/structlog event contains NO password, hash, or token value (depends on T011)

**Checkpoint**: cook identity backbone ready.

---

## Phase 3: User Story 1 - A cook signs in and owns their data (Priority: P1) 🎯 MVP

**Goal**: a cook logs in on the widget (no signup), the app is gated, and their profile/favorites/seen-history
are account-owned and isolated per cook.

**Independent Test**: seed two cook accounts directly; sign in as each via `/auth/login`; confirm gated
`/chat`/`/recipes`/`/favorites`/`/profile` work with the token and 401 without it (and with a legacy
`X-Profile-ID`); confirm each cook sees only their own favorites/profile.

### Tests for User Story 1 ⚠️ (write first, ensure they fail)

- [X] T015 [P] [US1] Integration test `tests/integration/test_cook_auth_gating.py`: `/auth/login` → JWT → gated `/chat` 200; no token → 401; legacy `X-Profile-ID` → 401; `/health` open; `GET /auth/me` echoes the cook (depends on Phase 2)
- [X] T016 [P] [US1] Integration test `tests/integration/test_cook_data_isolation.py`: two seeded cooks each set a profile + save a favorite; neither can read the other's favorites/profile/seen-history (FR-005) (depends on Phase 2)

### Implementation for User Story 1

- [X] T017 [US1] Implement `app/api/user/auth.py`: `POST /auth/login` (unauthenticated — `authenticate` then `issue_session`; generic 401) and `GET /auth/me` (`require_cook`); register in `app/api/user/__init__.py` (depends on T010, T012)
- [X] T018 [US1] Gate cook endpoints: swap the `ProfileId`/`require_profile_id` dependency for `require_cook` in `app/api/user/chat.py`, `recipes.py`, `favorites.py`, `profile.py`; re-key the `/chat` slowapi limiter to the cook id (depends on T012)
- [X] T019 [US1] Update `app/repo/profiles.py` for the cook-account owner key: `get`/`upsert`/`ensure_exists` keep their shape (the owner key is now the cook id; the FK to `cook_accounts` is satisfied because the cook is authenticated) (depends on T006)
- [X] T020 [US1] Widget login + gating: add `widget/src/lib/session.js` (store/clear token, expiry-aware), `widget/src/components/Login.jsx` (professional screen, NO signup), gate + sign-out in `widget/src/App.jsx`; change `widget/src/api/client.js` to send `Authorization: Bearer` (drop `X-Profile-ID`; on 401 clear token → login); remove `widget/src/lib/profile.js` (depends on T017)

**Checkpoint**: cooks sign in, own their data, and the app is gated — testable with seeded accounts.

---

## Phase 4: User Story 2 - Admin provisions and manages cook accounts (Priority: P2)

**Goal**: an operator admin (008) creates/lists/deactivates/reactivates/resets cook accounts from the
dashboard; no self-service path exists.

**Independent Test**: with an 008 admin token, drive `/admin/cooks` (create/list/deactivate/reactivate/reset)
+ the 409/422 cases; a non-admin operator token → 403; confirm the dashboard page shows no signup control.

### Tests for User Story 2 ⚠️ (write first, ensure they fail)

- [X] T021 [P] [US2] Integration test `tests/integration/test_admin_cooks.py`: admin creates a cook (201), duplicate → 409, weak password → 422, list shows it, deactivate/reactivate/reset → 200, and a non-admin operator token → 403 on every route (depends on Phase 2 + 008 `require_admin`)

### Implementation for User Story 2

- [X] T022 [US2] Implement `app/api/admin/cooks.py`: `POST /admin/cooks`, `GET /admin/cooks`, `POST /admin/cooks/{username}/deactivate`, `/reactivate`, `/reset-password` — all `require_admin`; register in `app/api/admin/__init__.py`; map errors per [contracts/admin-cooks-api.md](contracts/admin-cooks-api.md) (depends on T011)
- [X] T023 [US2] Add the admin-only `dashboard/pages/5_cooks.py`: create-cook form, accounts table (username/status), deactivate/reactivate/reset actions via `admin_client()`; NO signup control (depends on T022)

**Checkpoint**: admins manage cook accounts from the dashboard.

---

## Phase 5: User Story 3 - Revocation, persistence & safety hold (Priority: P3)

**Goal**: deactivation revokes access promptly; refresh keeps a cook signed in; the wall still holds per
authenticated cook; CI/evals/demo authenticate as the seeded demo cook (FR-020).

**Independent Test**: deactivate a signed-in cook → login denied + live token rejected next request; refresh
keeps the cook signed in (widget); a nut-allergic cook never sees a nut recipe; CI evals pass authenticated as
the demo cook.

### Tests for User Story 3 ⚠️ (write first, ensure they fail)

- [X] T024 [P] [US3] Integration test `tests/integration/test_cook_revocation.py`: a deactivated cook's `/auth/login` → 401 and their previously-issued token → 401 on the next `/chat` call (FR-009) (depends on Phase 2)

### Implementation for User Story 3

- [X] T025 [US3] Authenticate CI/evals as the seeded demo cook: add a login helper the eval runner (`evals/run_evals.py`) and the stack smoke test use to obtain a cook token and attach `Authorization: Bearer` on `/chat` calls; seed `COOK_SESSION_KEY`/`DEMO_COOK_PASSWORD` in the CI env (FR-020) (depends on T010, T013)
- [X] T026 [US3] Confirm the wall holds per authenticated cook: extend/point the red-team + wall regression so probes run as a constrained demo cook (token-authenticated), asserting no allergen leak — recipes still leave only via `recipe_view` (no logic change expected) (depends on T018, T025)

**Checkpoint**: revocation, refresh-persistence, safety, and gated CI all verified.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T027 Retire the passwordless path: remove `require_profile_id` and all `X-Profile-ID` reads; delete `widget/src/lib/profile.js`; confirm nothing still references the header
- [X] T028 [P] Update `docs/SECURITY.md`: cook auth, domain isolation (separate key + `typ:"cook"`), total gating, the seeded demo cook
- [X] T029 [P] Update `docs/RUNBOOK.md`: seed `COOK_SESSION_KEY` + demo-cook password; how an admin manages cooks; demo/CI login; update the 007 demo scenario to sign in as the demo cook. ⚠️ Add a clear warning that the `0005` migration **irreversibly deletes** all existing anonymous profile/favorites/seen-history data (FR-015) — back up before applying on any env with real data
- [X] T030 [P] Update `docs/DECISIONS.md`: end-user accounts + total gating replacing the passwordless profile-ID (and why constitution 2.0.0)
- [X] T031 Update `CLAUDE.md`: change the "Cook identity is a passwordless profile-ID" convention + the turn-flow note to the cook-account model (keep it consistent with constitution 2.0.0)
- [X] T032 Run `make lint && make test && make evals`; confirm all green — including the red-team + redaction + RAG gates authenticated as the demo cook (SC-008) and **feature 008 unchanged**
- [X] T033 Run [quickstart.md](quickstart.md) steps 1–8 against `make up` to validate end-to-end on the running stack

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (P1)**: no dependencies.
- **Foundational (P2)**: depends on Setup **and on feature 008's `core/security`** — **blocks all stories**.
- **User Stories (P3–P5)**: each depends on Foundational; otherwise independent at the API/test level. Real
  seams: US2 needs 008's `require_admin`; US3's `T026` builds on US1's gating (T018) + the demo cook (T025).
- **Polish (P6)**: after the stories you intend to ship.

### Within each story

- Write the story's test (T015/T016 / T021 / T024) first and see it fail, then implement.
- Foundational model→repo→service→dep precede the story endpoints/UI.

### Parallel opportunities

- Setup: T001 ‖ T002.
- Foundational: T007 ‖ T008; T014 after its targets.
- Story tests T015 ‖ T016 ‖ T021 ‖ T024 can be written in parallel; US1 and US2 backend work is independent.

---

## Parallel Example: Foundational

```bash
# After T004–T006 (model + migration), these touch different files:
Task: "Create Pydantic schemas in app/schemas/cook_auth.py"            # T007
Task: "Extend app/core/security.py with cook token issue/decode"        # T008
# Then, after the service/seam land:
Task: "Unit tests in tests/unit/test_cook_accounts.py"                  # T014
```

---

## Implementation Strategy

### MVP scope

- **MVP = US1**: a cook signs in and owns their data on a gated app. Independently testable with seeded cook
  accounts. This is the headline value (a user account connected to the user's favorites/profile).
- Then **US2** (admin provisioning — how cooks come to exist in production), then **US3** (revocation +
  gated CI + safety), then Polish.

### Incremental delivery

1. Setup + Foundational → cook identity backbone (after 008 ships).
2. US1 → login + gated app + account-owned data (MVP).
3. US2 → admin cook-management on the dashboard.
4. US3 → revocation, refresh-persistence, demo-cook-authenticated CI, wall regression.
5. Polish → retire `X-Profile-ID`, docs, CLAUDE.md convention, full green gates + quickstart.

---

## Notes

- `[P]` = different files, no dependency on an incomplete task.
- Cook identity comes ONLY from the verified cook token (never a header/body id); `/auth/login` + `/health`
  are the only open routes; everything else cook-facing is `require_cook`; `/admin/cooks*` is 008
  `require_admin`.
- Never weaken an eval threshold to pass T032 — fix the cause (Constitution Development Workflow).
- Commit after each task or logical group; stop at any checkpoint to validate a story independently.
