"""Unit tests for the cook-account services + cook-session tokens (009) — no DB (T014/T014a).

A tiny in-memory fake replaces `repo.cook_accounts` so these pin the POLICY in isolation: duplicate
username (409), weak/empty password on create AND reset (422), deactivate/reactivate, reset rotating the
stored hash, enumeration-resistant authentication (unknown user vs wrong password both yield None),
deactivated-cook denial, and `bootstrap_demo_cook` idempotency. The token tests prove a cook JWT issues →
decodes round-trip and that domain isolation holds: a wrong key is rejected, and an operator (008) token is
refused by `decode_cook_token` (it lacks `typ:"cook"`). T014a additionally proves every mutating admin
action emits an audit line recording action + actor and leaks NO password, hash, or token (FR-016).
"""

from __future__ import annotations

import uuid
from typing import Any

import jwt
import pytest
from app.core import security
from app.core.errors import AppError
from app.models.cook_account import CookAccount
from app.models.operator_account import OperatorAccount
from app.services.admin import cook_accounts as admin_svc
from app.services.user import cook_auth as auth_svc
from structlog.testing import capture_logs

# A throwaway session — the fake repo ignores it, but the services still thread it through.
_SESSION: Any = object()

# A fixed HS256 key for the token round-trip/isolation tests (the real key is COOK_SESSION_KEY from Vault).
# ≥32 bytes to satisfy PyJWT's SHA-256 key-length recommendation (keeps the test output warning-free).
_COOK_KEY = "unit-test-cook-signing-key-0123456789abcdef"


class _FakeRepo:
    """In-memory stand-in for repo.cook_accounts: username → CookAccount, insertion-ordered."""

    def __init__(self) -> None:
        """Start with no cooks."""
        self._rows: dict[str, CookAccount] = {}
        self._order: list[str] = []

    def get(self, _session: Any, account_id: str) -> CookAccount | None:
        """Return the cook with this id (the owner key), or None."""
        return next((c for c in self._rows.values() if c.id == account_id), None)

    def get_by_username(self, _session: Any, username: str) -> CookAccount | None:
        """Return the cook for this username, or None."""
        return self._rows.get(username)

    def list_all(self, _session: Any) -> list[CookAccount]:
        """Return all cooks newest-first (reverse insertion order)."""
        return [self._rows[u] for u in reversed(self._order)]

    def create(
        self,
        _session: Any,
        *,
        username: str,
        display_name: str | None,
        password_hash: str,
        created_by: str | None,
    ) -> CookAccount:
        """Insert and return a new cook (id + is_active set explicitly — there is no DB default here)."""
        cook = CookAccount(
            username=username,
            display_name=display_name,
            password_hash=password_hash,
            created_by=created_by,
        )
        cook.id = str(uuid.uuid4())
        cook.is_active = True
        self._rows[username] = cook
        self._order.append(username)
        return cook

    def set_active(self, _session: Any, username: str, is_active: bool) -> CookAccount | None:
        """Toggle the active flag; return the row or None when unknown."""
        cook = self._rows.get(username)
        if cook is None:
            return None
        cook.is_active = is_active
        return cook

    def set_password(self, _session: Any, username: str, password_hash: str) -> CookAccount | None:
        """Replace the password hash; return the row or None when unknown."""
        cook = self._rows.get(username)
        if cook is None:
            return None
        cook.password_hash = password_hash
        return cook

    def exists_any(self, _session: Any) -> bool:
        """True when at least one cook exists."""
        return bool(self._rows)


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> _FakeRepo:
    """Install one fresh in-memory fake repo on BOTH cook services and hand it back to the test."""
    fake = _FakeRepo()
    monkeypatch.setattr(admin_svc, "repo", fake)
    monkeypatch.setattr(auth_svc, "repo", fake)
    return fake


# ── create (admin) ────────────────────────────────────────────────────────────────────────────────


def test_create_cook_hashes_and_defaults_display_name(repo: _FakeRepo) -> None:
    """A created cook stores a bcrypt hash (not plaintext) and defaults display_name to username."""
    cook = admin_svc.create_cook(
        _SESSION, username="alice", password="supersecret123", created_by="root"
    )
    assert cook.username == "alice"
    assert cook.display_name == "alice"
    assert cook.password_hash != "supersecret123"
    assert security.verify_password("supersecret123", cook.password_hash)


def test_create_duplicate_username_rejected(repo: _FakeRepo) -> None:
    """Creating a second cook with an existing username is a 409 cook_exists (FR-006)."""
    admin_svc.create_cook(_SESSION, username="alice", password="supersecret123")
    with pytest.raises(AppError) as exc:
        admin_svc.create_cook(_SESSION, username="alice", password="anothergoodpw")
    assert exc.value.status_code == 409
    assert exc.value.code == "cook_exists"


@pytest.mark.parametrize("weak", ["", "short"])
def test_create_weak_password_rejected(repo: _FakeRepo, weak: str) -> None:
    """An empty or <8-char password is a 422 weak_password on create (FR-007/FR-010)."""
    with pytest.raises(AppError) as exc:
        admin_svc.create_cook(_SESSION, username="bob", password=weak)
    assert exc.value.status_code == 422
    assert exc.value.code == "weak_password"


# ── deactivate / reactivate ──────────────────────────────────────────────────────────────────────


def test_deactivate_then_reactivate(repo: _FakeRepo) -> None:
    """Deactivate flips is_active off; reactivate flips it back on (no last-admin guard — cooks are role-less)."""
    admin_svc.create_cook(_SESSION, username="worker", password="supersecret123")
    assert admin_svc.deactivate(_SESSION, "worker").is_active is False
    assert admin_svc.reactivate(_SESSION, "worker").is_active is True


@pytest.mark.parametrize("action", ["deactivate", "reactivate"])
def test_deactivate_reactivate_unknown_is_404(repo: _FakeRepo, action: str) -> None:
    """Toggling an unknown username is a 404 cook_not_found."""
    with pytest.raises(AppError) as exc:
        getattr(admin_svc, action)(_SESSION, "ghost")
    assert exc.value.status_code == 404
    assert exc.value.code == "cook_not_found"


# ── reset password (admin) ───────────────────────────────────────────────────────────────────────


def test_reset_password_changes_hash(repo: _FakeRepo) -> None:
    """Reset replaces the stored hash so the new password verifies and the old one no longer does."""
    cook = admin_svc.create_cook(_SESSION, username="alice", password="originalpass1")
    old_hash = cook.password_hash
    admin_svc.reset_password(_SESSION, "alice", "brandnewpass1")
    assert cook.password_hash != old_hash
    assert security.verify_password("brandnewpass1", cook.password_hash)
    assert not security.verify_password("originalpass1", cook.password_hash)


@pytest.mark.parametrize("weak", ["", "short"])
def test_reset_weak_password_rejected(repo: _FakeRepo, weak: str) -> None:
    """The <8-char rule applies to reset too — a weak new password is a 422 (FR-010)."""
    admin_svc.create_cook(_SESSION, username="alice", password="originalpass1")
    with pytest.raises(AppError) as exc:
        admin_svc.reset_password(_SESSION, "alice", weak)
    assert exc.value.status_code == 422
    assert exc.value.code == "weak_password"


def test_reset_unknown_is_404(repo: _FakeRepo) -> None:
    """Resetting an unknown cook (with a valid password) is a 404 cook_not_found."""
    with pytest.raises(AppError) as exc:
        admin_svc.reset_password(_SESSION, "ghost", "validpassword1")
    assert exc.value.status_code == 404


# ── authenticate (enumeration-resistant) ─────────────────────────────────────────────────────────


def test_authenticate_valid_credentials(repo: _FakeRepo) -> None:
    """Correct credentials for an active cook return that cook."""
    admin_svc.create_cook(_SESSION, username="alice", password="supersecret123")
    cook = auth_svc.authenticate(_SESSION, "alice", "supersecret123")
    assert cook is not None and cook.username == "alice"


def test_authenticate_unknown_user_and_wrong_password_both_none(repo: _FakeRepo) -> None:
    """An unknown username and a wrong password BOTH return None — one generic outcome (FR-008)."""
    admin_svc.create_cook(_SESSION, username="alice", password="supersecret123")
    assert auth_svc.authenticate(_SESSION, "nobody", "supersecret123") is None
    assert auth_svc.authenticate(_SESSION, "alice", "wrongpassword") is None


def test_authenticate_disabled_cook_denied(repo: _FakeRepo) -> None:
    """A correct password for a deactivated cook still returns None (a disabled cook cannot sign in)."""
    admin_svc.create_cook(_SESSION, username="worker", password="supersecret123")
    admin_svc.deactivate(_SESSION, "worker")
    assert auth_svc.authenticate(_SESSION, "worker", "supersecret123") is None


# ── bootstrap demo cook (idempotent) ─────────────────────────────────────────────────────────────


def test_bootstrap_demo_cook_seeds_once_then_is_noop(repo: _FakeRepo) -> None:
    """Bootstrap seeds the demo cook once and is a no-op on any later call (FR-020)."""
    created = auth_svc.bootstrap_demo_cook(_SESSION, username="demo", password="souschef-demo")
    assert created is not None and created.username == "demo"
    assert created.created_by is None
    again = auth_svc.bootstrap_demo_cook(_SESSION, username="demo", password="souschef-demo")
    assert again is None
    assert len(repo.list_all(_SESSION)) == 1


# ── cook-session tokens (issue → decode + domain isolation) ──────────────────────────────────────


def test_cook_token_round_trips(repo: _FakeRepo) -> None:
    """A minted cook token decodes back to the cook's id under `sub` with `typ:"cook"`."""
    cook = admin_svc.create_cook(_SESSION, username="alice", password="supersecret123")
    token = security.issue_cook_token(cook, signing_key=_COOK_KEY, ttl_minutes=480)
    claims = security.decode_cook_token(token, signing_key=_COOK_KEY)
    assert claims["sub"] == cook.id
    assert claims["typ"] == "cook"


def test_cook_token_wrong_key_rejected(repo: _FakeRepo) -> None:
    """A cook token verified with a different key is rejected (key isolation, FR-013)."""
    cook = admin_svc.create_cook(_SESSION, username="alice", password="supersecret123")
    token = security.issue_cook_token(cook, signing_key=_COOK_KEY, ttl_minutes=480)
    with pytest.raises(jwt.InvalidTokenError):
        security.decode_cook_token(token, signing_key="a-different-key-0123456789abcdefghijklmnop")


def test_operator_token_rejected_by_decode_cook_token() -> None:
    """An operator (008) token — no `typ:"cook"` — is refused by decode_cook_token (domain isolation, FR-013).

    Even signed with the same key, the operator token carries `role`, not `typ:"cook"`, so the cook decoder
    rejects it: an operator session can never be replayed as a cook session.
    """
    operator = OperatorAccount(username="admin", role="admin", password_hash="x")
    operator_token = security.issue_token(operator, signing_key=_COOK_KEY, ttl_minutes=480)
    with pytest.raises(jwt.InvalidTokenError):
        security.decode_cook_token(operator_token, signing_key=_COOK_KEY)


# ── audit logging (T014a / FR-016) ───────────────────────────────────────────────────────────────


def test_create_emits_redaction_safe_audit_line(repo: _FakeRepo) -> None:
    """create_cook logs an audit event with action + actor + target and NO password/hash value."""
    with capture_logs() as logs:
        cook = admin_svc.create_cook(
            _SESSION, username="alice", password="supersecret123", created_by="root"
        )
    events = [e for e in logs if e.get("event") == "cook_account_created"]
    assert len(events) == 1
    entry = events[0]
    assert entry["action"] == "create"
    assert entry["actor"] == "root"
    assert entry["target"] == "alice"
    serialized = str(logs)
    assert "supersecret123" not in serialized
    assert cook.password_hash not in serialized


def test_deactivate_and_reset_emit_redaction_safe_audit_lines(repo: _FakeRepo) -> None:
    """deactivate + reset_password each log an audit line recording the actor, leaking no secret (FR-016)."""
    cook = admin_svc.create_cook(_SESSION, username="worker", password="supersecret123")
    with capture_logs() as logs:
        admin_svc.deactivate(_SESSION, "worker", actor="admin1")
        admin_svc.reset_password(_SESSION, "worker", "rotatedpass99", actor="admin1")

    actions = {e.get("event"): e for e in logs}
    assert actions["cook_account_deactivated"]["actor"] == "admin1"
    assert actions["cook_account_deactivated"]["target"] == "worker"
    assert actions["cook_account_password_reset"]["actor"] == "admin1"
    serialized = str(logs)
    assert "rotatedpass99" not in serialized
    assert cook.password_hash not in serialized
