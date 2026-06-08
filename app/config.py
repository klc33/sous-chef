"""Application configuration: typed, non-secret bootstrap values from the environment.

Secrets are NOT here — they come from Vault at runtime (see app/infra/vault.py). This
module only holds the locations and switches needed to *reach* Vault and the backing
services, validated once at startup so misconfiguration fails fast (FR-010).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Non-secret settings loaded from environment / .env at process start.

    Each field maps to an env var (case-insensitive). Required fields have no default, so a
    missing value raises a ValidationError at construction — that is the fail-fast guarantee.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Environment name ("local" | "production"); informational + non-secret.
    env: str = Field(default="local")

    # Application version surfaced by /health; non-secret.
    version: str = Field(default="0.1.0")

    # Vault bootstrap: address + token (the token is a throwaway non-secret dev value locally;
    # in production the platform injects it — it is never a committed secret).
    vault_addr: str
    vault_token: str

    # Backing-service locations (not credentials).
    postgres_url: str
    redis_url: str

    # Tracing collector endpoint.
    phoenix_collector_endpoint: str


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide singleton Settings, constructed (and validated) on first call.

    lru_cache means the environment is read and validated exactly once; later callers get the
    same instance. A missing required field raises here, aborting startup (FR-010).
    """
    return Settings()
