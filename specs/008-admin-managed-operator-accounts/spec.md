# Feature Specification: Admin-Managed Operator Accounts

**Feature Branch**: `008-admin-managed-operator-accounts`

**Created**: 2026-06-15

**Status**: Draft

**Input**: User description: "Replace the Streamlit operator dashboard's single hardcoded-operator login with an admin-managed, multi-user account system. Users can ONLY sign in (no self-registration); a professional login UI; only an admin can create new users; a bootstrapped initial admin; a Users management page; per-user durable credentials replacing the single operator hash; cookie-persisted sessions preserved. The cook-facing widget and its passwordless profile-ID are out of scope and must not break."

## Overview

The operator dashboard today admits a **single** operator using one shared credential (one username plus
one stored password hash). That makes access all-or-nothing: it cannot be granted to a second person, and
it cannot be revoked from one person without changing the shared secret for everyone. This feature replaces
that single-credential model with **named, admin-managed accounts**: each operator has their own account
with a role, an admin can provision and revoke accounts individually, and there is **no self-service
registration** — accounts exist only because an admin created them.

**Scope boundary (must not break):** This concerns the **operator dashboard** only. The cook-facing chat
widget, its passwordless `X-Profile-ID` identity, and the public chat boundary are explicitly out of scope
and must continue to work unchanged. This is operator-console access control, not cook/end-user
authentication.

## Clarifications

### Session 2026-06-15

- Q: Session / token lifetime (the "session window" in FR-013)? → A: 8 hours — JWT `exp` and the dashboard
  cookie window are both set to 8 hours (one workday); refresh stays signed in within that window.
- Q: Is there a password-recovery path given self-service change is deferred? → A: Yes — an admin can reset
  any account's password (admin-only); there is still no self-service change in v1.
- Q: Can an admin change an existing account's role (promote/demote)? → A: No — roles are fixed at creation
  in v1; changing a role means creating a new account. Out of scope.
- Q: What is the password strength rule (FR-010)? → A: Minimum 8 characters, no other composition rules
  (applies to both creation and admin reset).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Admin provisions and revokes operator accounts (Priority: P1)

An administrator signs in to the dashboard, opens a **Users** management area, and creates a new operator
account by entering a username, an initial password, and a role. The new account immediately appears in a
list of existing accounts showing each account's role and status. When someone should no longer have
access, the admin **deactivates** that account; the account stays listed (for audit) but can no longer sign
in. There is no path anywhere — for the admin or anyone else — to "sign up" a brand-new account; accounts
are only ever created by an admin from this area.

**Why this priority**: This is the headline capability and the reason the feature exists — granting and
revoking individual access. Without it the system is still effectively single-user.

**Independent Test**: Sign in as the bootstrapped admin, create a new account, confirm it appears in the
list, then deactivate it and confirm it is shown as disabled. Fully demonstrable on its own.

**Acceptance Scenarios**:

1. **Given** an admin is signed in, **When** they open the Users area and submit a valid new username,
   initial password, and role, **Then** the account is created and appears in the account list with that
   role and an "active" status.
2. **Given** an admin is signed in, **When** they deactivate an existing active account, **Then** the
   account is shown as disabled and the affected person can no longer sign in.
3. **Given** an admin is viewing the Users area, **When** they try to create an account with a username that
   already exists, **Then** creation is rejected with a clear message and no duplicate account is created.
4. **Given** an admin is signed in, **When** they look anywhere in the dashboard or login screen, **Then**
   no self-service registration / sign-up option is presented.

---

### User Story 2 - A provisioned operator signs in (login-only) (Priority: P2)

A person who has been given an account by an admin opens the dashboard and is met with a **professional
login screen**. They enter their username and password and reach the operator console. A non-admin operator
can use the console's operational pages but **cannot see or use** the Users management area. There is no way
for them to register themselves — if they have no account, an admin must create one.

**Why this priority**: Login is the everyday entry point for every non-admin operator, and the role split
(can operate, cannot manage users) is what makes "admin-only user creation" real rather than nominal.

**Independent Test**: With an admin-created non-admin account, sign in through the login screen, confirm the
console loads, and confirm the Users management area is absent / inaccessible to that account.

**Acceptance Scenarios**:

1. **Given** a valid active account, **When** the person enters correct credentials on the login screen,
   **Then** they are signed in and the operator console is shown.
2. **Given** a valid active **non-admin** account, **When** that person is signed in, **Then** the Users
   management area is not available to them and cannot be reached.
3. **Given** any account, **When** the person enters an incorrect password (or a username that does not
   exist), **Then** sign-in fails with a single generic message that does not reveal whether the username
   exists.
4. **Given** a **deactivated** account, **When** that person attempts to sign in with otherwise-correct
   credentials, **Then** sign-in is denied.

---

### User Story 3 - Sessions persist and access is always recoverable (Priority: P3)

A signed-in operator refreshes the browser (or reopens the tab within the session window) and remains
signed in rather than being kicked back to the login screen. On a brand-new deployment where no accounts
exist yet, an **initial admin account is bootstrapped automatically** so there is always a first way in;
the bootstrap never creates a second admin if one already exists. The system never allows itself to be
locked out of administration — the last remaining active admin cannot be deactivated.

**Why this priority**: These are the safety and continuity properties around the core flows. They matter
for a usable demo (refresh doesn't log you out) and for not bricking access, but they sit behind the two
flows above.

**Independent Test**: Sign in, refresh the page, confirm you stay signed in; on a fresh environment confirm
the bootstrap admin can sign in; attempt to deactivate the only admin and confirm it is prevented.

**Acceptance Scenarios**:

1. **Given** a signed-in operator, **When** they refresh the browser within the session window, **Then**
   they remain signed in and are not returned to the login screen.
2. **Given** a fresh deployment with no accounts, **When** the system starts, **Then** exactly one initial
   admin account exists and can sign in.
3. **Given** an environment that already has an admin, **When** the system starts again, **Then** no
   duplicate bootstrap admin is created.
4. **Given** exactly one active admin account, **When** an admin attempts to deactivate it, **Then** the
   action is refused with a clear message so administration cannot be locked out.

---

### Edge Cases

- **Duplicate username**: creating an account whose username already exists is rejected (US1 #3).
- **Weak/empty credentials**: creating (or resetting) a password that is empty or shorter than 8 characters
  is rejected with a clear message (FR-010).
- **Account enumeration**: a wrong password and a non-existent username produce the *same* generic failure
  message (US2 #3).
- **Last-admin lockout**: the final active admin cannot be deactivated (US3 #4); likewise an admin cannot
  deactivate the account they are currently signed in with if it is the last admin.
- **Revoking an already-signed-in operator**: once an account is deactivated, that person is denied on their
  next sign-in attempt and their existing session stops granting access at its next page load.
- **Re-enabling**: a previously deactivated account can be reactivated by an admin and then sign in again.
- **Legacy migration**: on upgrading an environment that used the old single-operator credential, the old
  shared credential stops being a way in and is replaced by the bootstrapped admin account.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The dashboard MUST require a successful sign-in with a named account before any operator page
  is shown.
- **FR-002**: The system MUST present a professional, polished login screen as the entry point to the
  dashboard.
- **FR-003**: The system MUST NOT expose any self-service registration / sign-up path on the login screen or
  anywhere else in the dashboard.
- **FR-004**: Accounts MUST be created **only** by an account holding the admin role, from a dedicated user-
  management area.
- **FR-005**: Each account MUST carry a role of either **admin** (can manage users) or **user** (can operate
  the dashboard but cannot manage users).
- **FR-006**: An admin MUST be able to create a new account by supplying a username, an initial password,
  and a role.
- **FR-007**: An admin MUST be able to view a list of all accounts showing each account's username, role,
  and active/disabled status.
- **FR-008**: An admin MUST be able to deactivate (disable) and reactivate (enable) an account; deactivation
  MUST prevent that account from signing in.
- **FR-008a**: An admin MUST be able to reset another account's password (admin-only); the new password is
  subject to the same strength rule as creation (FR-010). There is no self-service password change in v1.
- **FR-009**: Usernames MUST be unique; an attempt to create an account with an existing username MUST be
  rejected without creating a duplicate.
- **FR-010**: The system MUST reject any password shorter than 8 characters (and an empty password) on both
  account creation and admin password reset; no other composition rules are imposed.
- **FR-011**: A non-admin account MUST NOT be able to view or use the user-management area by any path.
- **FR-012**: A sign-in attempt with incorrect credentials or a non-existent username MUST fail with a
  single generic message that does not disclose whether the username exists.
- **FR-013**: A signed-in operator MUST remain signed in across a browser refresh within the session window
  (the session is not lost on reload).
- **FR-014**: The system MUST bootstrap exactly one initial admin account when no account yet exists, and
  MUST NOT create a duplicate bootstrap admin when an admin already exists.
- **FR-015**: The system MUST prevent deactivation of the last remaining active admin so administration
  cannot be locked out.
- **FR-016**: Account credentials MUST be stored durably; passwords MUST be stored only as irreversible
  (one-way) hashes, never in recoverable form.
- **FR-017**: This account model MUST replace the previous single-operator credential model; the previous
  single shared operator credential MUST cease to grant access once accounts exist.
- **FR-018**: User-management actions (account created, role assigned, account enabled/disabled) MUST be
  recorded for audit, with no sensitive material (passwords/hashes/secrets) written to logs or traces.
- **FR-019**: The cook-facing chat widget, its passwordless profile-ID identity, and the public chat
  boundary MUST remain unchanged and unaffected by this feature.
- **FR-020**: The feature MUST preserve existing safety and stack guarantees — the allergen wall, grounding,
  the red-team and redaction gates, hosted-only inference, and the no-`torch`/secrets-in-Vault discipline —
  none of which this feature weakens.

### Key Entities *(include if feature involves data)*

- **Operator Account**: a named login identity for the dashboard. Key attributes: unique username, display
  name, role (admin | user), status (active | disabled), an irreversible password hash, and audit timestamps
  (created, last updated) plus which admin created it. Replaces the prior single-operator credential.
- **Role**: the permission level of an account — **admin** (may manage accounts) or **user** (may operate
  the dashboard but not manage accounts). Determines access to the user-management area.
- **Operator Session**: the authenticated state established at sign-in that survives a browser refresh
  within a bounded session window, and ends on sign-out, expiry, or when the underlying account is disabled.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An admin can create a new, working operator account in under 2 minutes, and the new account
  can sign in successfully on its first attempt.
- **SC-002**: There are **zero** self-service registration entry points reachable from the login screen or
  the dashboard.
- **SC-003**: A signed-in operator who refreshes the page remains signed in 100% of the time within the
  session window (the prior "logged out on every refresh" failure does not occur).
- **SC-004**: A deactivated account is denied access on 100% of subsequent sign-in attempts.
- **SC-005**: A non-admin account has access to **zero** user-management functions.
- **SC-006**: Administration can never be fully locked out: at least one active admin account always exists
  (the last active admin cannot be removed/disabled), and a fresh environment always has a working admin.
- **SC-007**: Existing cook-facing behavior shows **no** regression — the public chat flow, favorites,
  freshness, the wall, and the redaction/red-team gates all continue to pass unchanged.

## Assumptions

- **Operator-console auth, not end-user auth.** This extends the *operator dashboard* access already in the
  approved stack; it is **not** cook/end-user authentication, which remains out of scope. Cook identity
  stays the passwordless profile-ID. This keeps the change consistent with the project's prohibition on
  full end-user authentication.
- **Two roles only** for v1 — `admin` and `user`. Both roles can use all non-management operator pages;
  only `admin` can reach user management. Finer-grained permissions are a future concern.
- **Roles are fixed at creation in v1** (resolved in Clarifications 2026-06-15). There is no role-change
  (promote/demote) operation; re-grading access means creating a new account. This also avoids a second
  administration-lockout vector (a demotion of the last admin).
- **Authentication is username + password.** Email/display name is optional account metadata, not a second
  login factor. SSO/OAuth/MFA are out of scope for v1.
- **Bootstrap admin** is seeded on first run from a configured initial credential (sourced via the project's
  existing secret-management discipline), and this replaces the old single-operator username/hash. The
  initial admin is expected to provision other accounts.
- **Durable, hashed credentials.** Accounts and their metadata are stored in the project's existing primary
  datastore; passwords are stored only as one-way hashes. The session cookie-signing key continues to live
  in the secret store (no secrets in `.env`, code, or images).
- **Deactivation, not hard delete**, is the revocation mechanism for v1; disabled accounts are retained so
  the audit trail is preserved. Hard deletion is out of scope.
- **Self-service password change/reset is out of scope for v1.** The recovery path is admin-only: an admin
  resets an account's password (FR-008a). A future iteration may add self-service password change.
- **Session window is 8 hours** (one workday). The login token's expiry and the dashboard cookie window are
  both set to 8 hours; a refresh stays signed in within that window, then the operator signs in again
  (resolved in Clarifications 2026-06-15).
- **No new external technology** is introduced; the feature reuses the existing dashboard auth library,
  primary datastore, and secret store. No `torch`, no new datastore, no microservice.

## Dependencies

- The operator dashboard surface and its existing cookie-persistence behavior.
- The project's primary datastore (for durable account storage) and its migration mechanism.
- The project's secret store (for the cookie-signing key and the bootstrap admin credential).
- The existing operator/admin boundary between the dashboard and the backend admin API (unchanged by this
  feature except that the human signing in is now a named account rather than the single operator).
