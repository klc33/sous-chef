"""Unit tests for the operator-account service (services/admin/operator_accounts.py) — no DB (T014/T014a).

The repo is replaced with a tiny in-memory fake so these pin the service's POLICY in isolation: duplicate
username (409), weak/empty password on create AND reset (422), deactivate/reactivate, the last-active-admin
lockout guard (409), reset rotating the stored hash, bootstrap idempotency, and enumeration-resistant
authentication (unknown user vs wrong password both yield None). T014a additionally proves every mutating
action emits an audit line that records the action + actor and leaks NO password, hash, or token (FR-018).
"""

from __future__ import annotations

from typing import Any

import pytest
from app.core import security
from app.core.errors import AppError
from app.models.operator_account import OperatorAccount
from app.services.admin import operator_accounts as svc
from structlog.testing import capture_logs

# A throwaway session — the fake repo ignores it, but the service still threads it through.
_SESSION: Any = object()


class _FakeRepo:
    """In-memory stand-in for repo.operator_accounts: username → OperatorAccount, insertion-ordered."""

    def __init__(self) -> None:
        """Start with no accounts."""
        self._rows: dict[str, OperatorAccount] = {}
        self._order: list[str] = []

    def get_by_username(self, _session: Any, username: str) -> OperatorAccount | None:
        """Return the account for this username, or None."""
        return self._rows.get(username)

    def list_all(self, _session: Any) -> list[OperatorAccount]:
        """Return all accounts newest-first (reverse insertion order)."""
        return [self._rows[u] for u in reversed(self._order)]

    def create(
        self,
        _session: Any,
        *,
        username: str,
        display_name: str | None,
        role: str,
        password_hash: str,
        created_by: str | None,
    ) -> OperatorAccount:
        """Insert and return a new account (is_active set explicitly, since there is no DB default here)."""
        account = OperatorAccount(
            username=username,
            display_name=display_name,
            role=role,
            password_hash=password_hash,
            created_by=created_by,
        )
        account.is_active = True
        self._rows[username] = account
        self._order.append(username)
        return account

    def set_active(self, _session: Any, username: str, is_active: bool) -> OperatorAccount | None:
        """Toggle the active flag; return the row or None when unknown."""
        account = self._rows.get(username)
        if account is None:
            return None
        account.is_active = is_active
        return account

    def set_password(
        self, _session: Any, username: str, password_hash: str
    ) -> OperatorAccount | None:
        """Replace the password hash; return the row or None when unknown."""
        account = self._rows.get(username)
        if account is None:
            return None
        account.password_hash = password_hash
        return account

    def count_active_admins(self, _session: Any) -> int:
        """Count active admin accounts (the last-admin guard input)."""
        return sum(1 for a in self._rows.values() if a.role == "admin" and a.is_active)

    def exists_any(self, _session: Any) -> bool:
        """True when at least one account exists."""
        return bool(self._rows)


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> _FakeRepo:
    """Install a fresh in-memory fake repo on the service module and hand it back to the test."""
    fake = _FakeRepo()
    monkeypatch.setattr(svc, "repo", fake)
    return fake


# ── create ──────────────────────────────────────────────────────────────────────────────────────


def test_create_account_hashes_and_defaults_display_name(repo: _FakeRepo) -> None:
    """A created account stores a bcrypt hash (not plaintext) and defaults display_name to username."""
    account = svc.create_account(
        _SESSION, username="alice", password="supersecret123", role="admin", created_by="root"
    )
    assert account.username == "alice"
    assert account.display_name == "alice"
    assert account.password_hash != "supersecret123"
    assert security.verify_password("supersecret123", account.password_hash)


def test_create_duplicate_username_rejected(repo: _FakeRepo) -> None:
    """Creating a second account with an existing username is a 409 user_exists (FR-009)."""
    svc.create_account(_SESSION, username="alice", password="supersecret123", role="user")
    with pytest.raises(AppError) as exc:
        svc.create_account(_SESSION, username="alice", password="anothergoodpw", role="user")
    assert exc.value.status_code == 409
    assert exc.value.code == "user_exists"


@pytest.mark.parametrize("weak", ["", "short"])
def test_create_weak_password_rejected(repo: _FakeRepo, weak: str) -> None:
    """An empty or <8-char password is a 422 weak_password on create (FR-010)."""
    with pytest.raises(AppError) as exc:
        svc.create_account(_SESSION, username="bob", password=weak, role="user")
    assert exc.value.status_code == 422
    assert exc.value.code == "weak_password"


# ── deactivate / reactivate / last-admin guard ───────────────────────────────────────────────────


def test_deactivate_then_reactivate(repo: _FakeRepo) -> None:
    """Deactivate flips is_active off (with another admin present); reactivate flips it back on."""
    svc.create_account(_SESSION, username="admin1", password="supersecret123", role="admin")
    svc.create_account(_SESSION, username="worker", password="supersecret123", role="user")
    deactivated = svc.deactivate(_SESSION, "worker")
    assert deactivated.is_active is False
    reactivated = svc.reactivate(_SESSION, "worker")
    assert reactivated.is_active is True


def test_deactivate_unknown_is_404(repo: _FakeRepo) -> None:
    """Deactivating an unknown username is a 404 user_not_found."""
    with pytest.raises(AppError) as exc:
        svc.deactivate(_SESSION, "ghost")
    assert exc.value.status_code == 404
    assert exc.value.code == "user_not_found"


def test_last_active_admin_cannot_be_deactivated(repo: _FakeRepo) -> None:
    """Deactivating the only active admin is refused with 409 last_admin (FR-015 / SC-006)."""
    svc.create_account(_SESSION, username="solo", password="supersecret123", role="admin")
    with pytest.raises(AppError) as exc:
        svc.deactivate(_SESSION, "solo")
    assert exc.value.status_code == 409
    assert exc.value.code == "last_admin"


def test_second_admin_lets_first_be_deactivated(repo: _FakeRepo) -> None:
    """With two active admins the guard does not fire — one can be deactivated."""
    svc.create_account(_SESSION, username="admin1", password="supersecret123", role="admin")
    svc.create_account(_SESSION, username="admin2", password="supersecret123", role="admin")
    assert svc.deactivate(_SESSION, "admin1").is_active is False


# ── reset password ───────────────────────────────────────────────────────────────────────────────


def test_reset_password_changes_hash(repo: _FakeRepo) -> None:
    """Reset replaces the stored hash so the new password verifies and the old one no longer does."""
    account = svc.create_account(
        _SESSION, username="alice", password="originalpass1", role="user"
    )
    old_hash = account.password_hash
    svc.reset_password(_SESSION, "alice", "brandnewpass1")
    assert account.password_hash != old_hash
    assert security.verify_password("brandnewpass1", account.password_hash)
    assert not security.verify_password("originalpass1", account.password_hash)


@pytest.mark.parametrize("weak", ["", "short"])
def test_reset_weak_password_rejected(repo: _FakeRepo, weak: str) -> None:
    """The <8-char rule applies to reset too — a weak new password is a 422 (FR-010/FR-008a)."""
    svc.create_account(_SESSION, username="alice", password="originalpass1", role="user")
    with pytest.raises(AppError) as exc:
        svc.reset_password(_SESSION, "alice", weak)
    assert exc.value.status_code == 422
    assert exc.value.code == "weak_password"


def test_reset_unknown_is_404(repo: _FakeRepo) -> None:
    """Resetting an unknown account (with a valid password) is a 404 user_not_found."""
    with pytest.raises(AppError) as exc:
        svc.reset_password(_SESSION, "ghost", "validpassword1")
    assert exc.value.status_code == 404


# ── authenticate (enumeration-resistant) ─────────────────────────────────────────────────────────


def test_authenticate_valid_credentials(repo: _FakeRepo) -> None:
    """Correct credentials for an active account return that account."""
    svc.create_account(_SESSION, username="alice", password="supersecret123", role="admin")
    account = svc.authenticate(_SESSION, "alice", "supersecret123")
    assert account is not None and account.username == "alice"


def test_authenticate_unknown_user_and_wrong_password_both_none(repo: _FakeRepo) -> None:
    """An unknown username and a wrong password BOTH return None — one generic outcome (FR-012)."""
    svc.create_account(_SESSION, username="alice", password="supersecret123", role="admin")
    assert svc.authenticate(_SESSION, "nobody", "supersecret123") is None
    assert svc.authenticate(_SESSION, "alice", "wrongpassword") is None


def test_authenticate_disabled_account_denied(repo: _FakeRepo) -> None:
    """A correct password for a deactivated account still returns None (a disabled account cannot sign in)."""
    svc.create_account(_SESSION, username="admin1", password="supersecret123", role="admin")
    svc.create_account(_SESSION, username="worker", password="supersecret123", role="user")
    svc.deactivate(_SESSION, "worker")
    assert svc.authenticate(_SESSION, "worker", "supersecret123") is None


# ── bootstrap (idempotent) ───────────────────────────────────────────────────────────────────────


def test_bootstrap_seeds_one_admin_then_is_noop(repo: _FakeRepo) -> None:
    """Bootstrap seeds exactly one admin on an empty table and is a no-op on any later call (FR-014)."""
    created = svc.bootstrap_admin(_SESSION, username="admin", password="supersecret123")
    assert created is not None and created.role == "admin"
    assert created.created_by is None
    again = svc.bootstrap_admin(_SESSION, username="admin", password="supersecret123")
    assert again is None
    assert len(repo.list_all(_SESSION)) == 1


def test_bootstrap_noop_when_any_account_exists(repo: _FakeRepo) -> None:
    """Bootstrap does nothing (creates no admin) when any account already exists."""
    svc.create_account(_SESSION, username="someone", password="supersecret123", role="user")
    assert svc.bootstrap_admin(_SESSION, username="admin", password="supersecret123") is None


# ── audit logging (T014a / FR-018) ───────────────────────────────────────────────────────────────


def test_create_emits_redaction_safe_audit_line(repo: _FakeRepo) -> None:
    """create logs an audit event with action + actor + target and NO password/hash value."""
    with capture_logs() as logs:
        account = svc.create_account(
            _SESSION, username="alice", password="supersecret123", role="admin", created_by="root"
        )
    events = [e for e in logs if e.get("event") == "operator_account_created"]
    assert len(events) == 1
    entry = events[0]
    assert entry["action"] == "create"
    assert entry["actor"] == "root"
    assert entry["target"] == "alice"
    serialized = str(logs)
    assert "supersecret123" not in serialized
    assert account.password_hash not in serialized


def test_deactivate_and_reset_emit_redaction_safe_audit_lines(repo: _FakeRepo) -> None:
    """deactivate + reset_password each log an audit line recording the actor, leaking no secret."""
    svc.create_account(_SESSION, username="admin1", password="supersecret123", role="admin")
    account = svc.create_account(_SESSION, username="worker", password="supersecret123", role="user")
    with capture_logs() as logs:
        svc.deactivate(_SESSION, "worker", actor="admin1")
        svc.reset_password(_SESSION, "worker", "rotatedpass99", actor="admin1")

    actions = {e.get("event"): e for e in logs}
    assert actions["operator_account_deactivated"]["actor"] == "admin1"
    assert actions["operator_account_deactivated"]["target"] == "worker"
    assert actions["operator_account_password_reset"]["actor"] == "admin1"
    serialized = str(logs)
    assert "rotatedpass99" not in serialized
    assert account.password_hash not in serialized
