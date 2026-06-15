"""Cook authentication + demo-cook bootstrap (009) — the cook-side login policy.

The one place the cook login policy lives (Constitution III): enumeration-resistant authentication (a dummy
bcrypt verify on a miss so timing never reveals which usernames exist; one generic `None` outcome), minting
the cook-session JWT, and the idempotent demo/eval cook seed (FR-020) so a fresh DB is demo-ready and CI can
log in. DB access goes through `repo.cook_accounts`; password + token primitives through `core.security`. No
password, hash, or token is ever logged.
"""

from __future__ import annotations

import structlog
from sqlalchemy.orm import Session

from app.core import security
from app.models.cook_account import CookAccount
from app.repo import cook_accounts as repo

log = structlog.get_logger()

# A fixed valid bcrypt hash to verify against when the username is unknown, so a missing account costs the
# same bcrypt work as a real one — login timing never reveals which usernames exist (FR-008).
_DUMMY_PASSWORD_HASH = security.hash_password("constant-time-dummy-password-never-matches")


def authenticate(session: Session, username: str, password: str) -> CookAccount | None:
    """Return the cook on valid credentials for an ACTIVE account, else None (one generic outcome).

    Enumeration-resistant (FR-008): an unknown username still runs a bcrypt verify against a dummy hash so
    timing does not reveal whether the username exists. A correct password for a disabled cook also returns
    None (a deactivated cook cannot sign in). The API maps None to a single generic 401.
    """
    cook = repo.get_by_username(session, username)
    if cook is None:
        security.verify_password(password, _DUMMY_PASSWORD_HASH)  # equalize timing; result discarded
        return None
    if not security.verify_password(password, cook.password_hash):
        return None
    if not cook.is_active:
        return None
    return cook


def issue_session(cook: CookAccount, *, signing_key: str, ttl_minutes: int) -> str:
    """Mint the cook-session JWT for a freshly-authenticated cook (delegates to core.security).

    The route reads `signing_key` (Vault `COOK_SESSION_KEY`) and `ttl_minutes` (`cook_jwt_ttl_minutes`) and
    passes them in, keeping this service free of Vault/config reach so it stays unit-testable.
    """
    return security.issue_cook_token(cook, signing_key=signing_key, ttl_minutes=ttl_minutes)


def bootstrap_demo_cook(session: Session, *, username: str, password: str) -> CookAccount | None:
    """Seed the demo/eval cook from Vault creds when it does not yet exist; a no-op otherwise (idempotent).

    Guarantees a migrated-but-empty DB is demo-ready on first boot and gives CI/evals + the live demo a real
    account to log in as (FR-020) — no bypass. Returns the created cook, or None when it already existed (so
    a restart never resets a changed password). Created with `created_by=None` (a system seed, not an admin
    action). The plaintext password is hashed here and never stored or logged.
    """
    if repo.get_by_username(session, username) is not None:
        return None
    cook = repo.create(
        session,
        username=username,
        display_name=username,
        password_hash=security.hash_password(password),
        created_by=None,
    )
    log.info("cook_account_bootstrapped", action="bootstrap", actor=None, target=username)
    return cook
