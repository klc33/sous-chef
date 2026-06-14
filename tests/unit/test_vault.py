"""Unit tests for the Vault secrets adapter — proving the fail-fast secret posture (US4 / SC-004).

These lock in the "remove a Vault key → backend fails fast at startup" half of T028 as a repeatable
gate rather than a one-off manual check: a missing/unseeded secret must raise StartupConfigError with
an actionable, seed-pointing message and never silently default (golden rule #4, FR-004/FR-014).

The hvac client is replaced with a small fake so no network/Vault is touched.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import app.infra.vault as vault_mod
import pytest
from app.core.errors import StartupConfigError
from app.infra.vault import VaultAdapter
from hvac.exceptions import InvalidPath


class _FakeKVv2:
    """Stand-in for client.secrets.kv.v2: returns preset data or raises a preset error."""

    def __init__(self, *, data: dict[str, str] | None, error: Exception | None) -> None:
        """Store either the secret payload to return or the exception to raise on read."""
        self._data = data
        self._error = error

    def read_secret_version(self, **_kwargs: Any) -> dict[str, Any]:
        """Mimic hvac's KV v2 read: raise the preset error or wrap data like the real response."""
        if self._error is not None:
            raise self._error
        return {"data": {"data": self._data}}


class _FakeClient:
    """Minimal hvac.Client replacement covering the methods VaultAdapter touches."""

    def __init__(
        self,
        *,
        authenticated: bool = True,
        data: dict[str, str] | None = None,
        error: Exception | None = None,
    ) -> None:
        """Wire authentication state plus the KV read outcome (data or raised error)."""
        self._authenticated = authenticated
        self.secrets = SimpleNamespace(kv=SimpleNamespace(v2=_FakeKVv2(data=data, error=error)))

    def is_authenticated(self) -> bool:
        """Report the preset authentication state."""
        return self._authenticated


def _settings() -> Any:
    """Non-secret settings the adapter constructor reads (address + bootstrap token only)."""
    return SimpleNamespace(vault_addr="http://vault:8200", vault_token="root")


def _adapter_with(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> VaultAdapter:
    """Build a VaultAdapter whose hvac.Client(...) returns the supplied fake client."""
    monkeypatch.setattr(vault_mod.hvac, "Client", lambda **_kw: client)
    return VaultAdapter(_settings())


_FULL_SECRETS = {
    "app_secret": "x",
    "GROQ_API_KEY": "g",
    "EMBEDDINGS_API_KEY": "e",
    "ADMIN_API_TOKEN": "t",
}


def test_load_secrets_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A seeded, authenticated Vault loads every key and serves them via get()."""
    adapter = _adapter_with(monkeypatch, _FakeClient(data=dict(_FULL_SECRETS)))
    adapter.load_secrets()
    assert adapter.get("GROQ_API_KEY") == "g"
    assert adapter.get("ADMIN_API_TOKEN") == "t"


def test_unauthenticated_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bad bootstrap token (is_authenticated False) fails fast pointing at VAULT_TOKEN."""
    adapter = _adapter_with(monkeypatch, _FakeClient(authenticated=False))
    with pytest.raises(StartupConfigError, match="VAULT_TOKEN"):
        adapter.load_secrets()


def test_unseeded_path_points_at_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    """An absent KV path (never seeded) raises with an actionable seed-pointing message (FR-014)."""
    adapter = _adapter_with(monkeypatch, _FakeClient(error=InvalidPath("nope")))
    with pytest.raises(StartupConfigError, match="not been seeded"):
        adapter.load_secrets()


def test_missing_required_admin_token_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """Removing the required ADMIN_API_TOKEN secret fails startup rather than booting unguarded."""
    partial = {k: v for k, v in _FULL_SECRETS.items() if k != "ADMIN_API_TOKEN"}
    adapter = _adapter_with(monkeypatch, _FakeClient(data=partial))
    with pytest.raises(StartupConfigError, match="ADMIN_API_TOKEN"):
        adapter.load_secrets()


def test_get_before_load_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """get() before load_secrets() is a clear error, never a silent empty value."""
    adapter = _adapter_with(monkeypatch, _FakeClient(data=dict(_FULL_SECRETS)))
    with pytest.raises(StartupConfigError, match="not loaded"):
        adapter.get("GROQ_API_KEY")


def test_get_unknown_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A loaded-but-absent key raises rather than silently defaulting (FR-004/FR-010)."""
    adapter = _adapter_with(monkeypatch, _FakeClient(data=dict(_FULL_SECRETS)))
    adapter.load_secrets()
    with pytest.raises(StartupConfigError, match="OPENAI_API_KEY"):
        adapter.get("OPENAI_API_KEY")
