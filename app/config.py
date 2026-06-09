"""Application configuration: typed, non-secret bootstrap values from the environment.

Secrets are NOT here — they come from Vault at runtime (see app/infra/vault.py). This
module only holds the locations and switches needed to *reach* Vault and the backing
services, validated once at startup so misconfiguration fails fast (FR-010).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The embedding dimension pinned by Alembic migration 0003 (`recipes.embedding vector(1536)`).
# Changing the model dimension requires a new migration, so we assert config matches this at
# startup (fail-fast) rather than letting a mismatched vector reach the DB and error mid-query.
MIGRATION_EMBEDDINGS_DIM = 1536


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

    # Tracing collector endpoint (Phoenix). Optional: when unset/empty, tracing is disabled and the
    # app runs untraced. Export is best-effort and must never block startup or requests, so a deploy
    # without a Phoenix collector simply turns tracing off rather than spamming export retries.
    phoenix_collector_endpoint: str | None = Field(default=None)

    # ── 003-intelligent-behavior: turn-pipeline tuning (all non-secret) ───────────────────────
    # Embeddings provider (OpenAI-compatible). Base URL + model are swappable without code changes;
    # the dimension is pinned to the migration (validated below). The API key is NOT here — it
    # comes from Vault (EMBEDDINGS_API_KEY).
    embeddings_base_url: str = Field(default="https://api.openai.com/v1")
    embeddings_model: str = Field(default="text-embedding-3-small")
    embeddings_dim: int = Field(default=MIGRATION_EMBEDDINGS_DIM)

    # Groq models (chat-only provider; embeddings come from the separate provider above). Two-model
    # split matching the turn fork: the workflow path (search ranking, nutrition, chitchat) uses the
    # fast/cheap `groq_model`; the bounded agent (meal-plan, multi-tool) uses the stronger
    # `groq_agent_model`. Splitting also gives each path its OWN Groq rate-limit bucket (limits are
    # per-model), which roughly doubles effective free-tier throughput and stops a heavy plan from
    # starving search turns.
    groq_model: str = Field(default="llama-3.1-8b-instant")
    groq_agent_model: str = Field(default="llama-3.3-70b-versatile")

    # Bounded-agent limits — the loop terminates when either is hit (Constitution VI / SC-007).
    agent_max_iterations: int = Field(default=5)
    agent_token_budget: int = Field(default=8000)

    # Router escalation: below this classifier confidence, the turn goes to the agent rather than
    # the deterministic workflow (see contracts/classifier.md).
    router_confidence_threshold: float = Field(default=0.55)

    # Vector-search over-fetch size: how many candidates to pull before the allergen wall trims to
    # the 3 displayed cards. Must exceed the display count so wall-compliant cards still surface.
    retrieval_candidate_pool: int = Field(default=20)

    @model_validator(mode="after")
    def _assert_embeddings_dim_matches_migration(self) -> Settings:
        """Fail fast when configured embedding dim diverges from the DB column the vectors land in.

        The pgvector column is fixed-width (`vector(1536)`); a mismatched `embeddings_dim` would only
        surface as an opaque error on the first write/search. Asserting at construction turns that
        into an immediate, legible startup failure (FR-010).
        """
        if self.embeddings_dim != MIGRATION_EMBEDDINGS_DIM:
            raise ValueError(
                f"embeddings_dim={self.embeddings_dim} does not match the migration's "
                f"vector({MIGRATION_EMBEDDINGS_DIM}); add a new migration to change the dimension."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide singleton Settings, constructed (and validated) on first call.

    lru_cache means the environment is read and validated exactly once; later callers get the
    same instance. A missing required field raises here, aborting startup (FR-010).
    """
    return Settings()
