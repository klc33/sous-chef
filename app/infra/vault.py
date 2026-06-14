"""HashiCorp Vault adapter (hvac): load all app secrets at startup, serve them in-process.

Secrets live ONLY in Vault (golden rule #4). At startup load_secrets() reads a KV v2 path into
memory; get(key) serves them to the rest of the app. Secret VALUES are never logged, and a
missing secret raises rather than silently defaulting (FR-004/FR-010).
"""

from __future__ import annotations

import hvac
from hvac.exceptions import InvalidPath

from app.config import VAULT_KEY_ADMIN_API_TOKEN, Settings
from app.core.errors import StartupConfigError

# KV v2 mount + path where scripts/seed_vault.sh writes the app's secrets.
_KV_MOUNT = "secret"
_SECRET_PATH = "sous-chef"


class VaultAdapter:
    """Wrapper over an hvac client holding secrets loaded once at startup."""

    def __init__(self, settings: Settings) -> None:
        """Build the hvac client from non-secret settings (address + bootstrap token)."""
        self._client = hvac.Client(url=settings.vault_addr, token=settings.vault_token)
        self._secrets: dict[str, str] = {}
        self._loaded = False

    def load_secrets(self) -> None:
        """Read all app secrets from the KV path into memory exactly once.

        Raises StartupConfigError if Vault is unreachable, unauthenticated, or the path is
        missing — fail fast rather than booting with no secrets. Never logs the values.
        """
        try:
            if not self._client.is_authenticated():
                raise StartupConfigError("Vault authentication failed (check VAULT_TOKEN)")
            try:
                resp = self._client.secrets.kv.v2.read_secret_version(
                    mount_point=_KV_MOUNT, path=_SECRET_PATH, raise_on_deleted_version=True
                )
            except InvalidPath:
                # The KV path is absent: Vault is reachable + authenticated but was never seeded.
                # Point the operator straight at the seed step rather than a generic transport error
                # (FR-014: missing secrets fail fast with a clear, actionable message).
                raise StartupConfigError(
                    f"Vault has no secrets at '{_KV_MOUNT}/{_SECRET_PATH}' — it has not been seeded. "
                    "Run `make seed` (local) or `scripts/seed_vault.sh` against the prod VAULT_ADDR."
                ) from None
            self._secrets = dict(resp["data"]["data"])
            self._loaded = True
            # The backend cannot guard /admin/* without the shared operator token, so its absence
            # is a startup error here rather than a late failure on the first admin request. The
            # dashboard-only secrets (password hash, cookie key) are validated by the dashboard
            # itself, since the backend never needs them (004-evals-and-uis data-model).
            if VAULT_KEY_ADMIN_API_TOKEN not in self._secrets:
                raise StartupConfigError(
                    f"Required secret '{VAULT_KEY_ADMIN_API_TOKEN}' not found in Vault "
                    "(run `make seed` / scripts/seed_vault.sh to write operator secrets)"
                )
        except StartupConfigError:
            raise
        except Exception as exc:  # hvac raises several connection/HTTP error types
            raise StartupConfigError(f"Could not load secrets from Vault: {exc}") from None

    def get(self, key: str) -> str:
        """Return a loaded secret by key.

        Raises a clear error if secrets were not loaded or the key is absent — a missing secret
        must never silently default (FR-004/FR-010).
        """
        if not self._loaded:
            raise StartupConfigError("Vault secrets not loaded; call load_secrets() at startup")
        try:
            return self._secrets[key]
        except KeyError:
            raise StartupConfigError(f"Secret '{key}' not found in Vault") from None

    def ping(self) -> bool:
        """Reachability check for /health: True when Vault answers, else False (no raise).

        Uses the unauthenticated /sys/init endpoint so it reports transport reachability and
        swallows connection errors so the health endpoint can render 'unreachable'.
        """
        try:
            return bool(self._client.sys.is_initialized())
        except Exception:
            return False
