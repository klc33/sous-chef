"""Application configuration: typed, non-secret bootstrap values from the environment.

Secrets are NOT here — they come from Vault at runtime (see app/infra/vault.py). This
module only holds the locations and switches needed to *reach* Vault and the backing
services, validated once at startup so misconfiguration fails fast (FR-010).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The embedding dimension pinned by Alembic migration 0003 (`recipes.embedding vector(1536)`).
# Changing the model dimension requires a new migration, so we assert config matches this at
# startup (fail-fast) rather than letting a mismatched vector reach the DB and error mid-query.
MIGRATION_EMBEDDINGS_DIM = 1536

# ── Operator-dashboard auth: canonical Vault KV key names (004-evals-and-uis) ──────────────
# The single operator's password HASH, the cookie-signing key, and the shared admin API token
# are SECRETS — they live only in Vault (golden rule #4), never in env or this file. Centralizing
# the key names here keeps the seed script (scripts/seed_vault.sh), the backend's admin_deps, and
# the Streamlit dashboard all reading the SAME keys. The username is non-secret (env field below).
VAULT_KEY_OPERATOR_PASSWORD_HASH = "OPERATOR_PASSWORD_HASH"
VAULT_KEY_DASHBOARD_COOKIE_KEY = "DASHBOARD_COOKIE_KEY"
VAULT_KEY_ADMIN_API_TOKEN = "ADMIN_API_TOKEN"
# Tracing API key — a SECRET, lives only in Vault (golden rule #4). Used when TRACING_PROVIDER=langsmith
# so the OTLP exporter can authenticate to LangSmith Cloud; self-hosted Phoenix needs no key.
VAULT_KEY_LANGSMITH_API_KEY = "LANGSMITH_API_KEY"


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
    # Redis is OPTIONAL. Its only product use is the operator dashboard's best-effort routing-split
    # counter (router.record_decision → admin metrics), which already no-ops when the cache is absent.
    # When REDIS_URL is unset the app runs with no cache and /health drops the redis check, so the Redis
    # service can be removed to free a Railway slot without touching the cook journey. See docs/RUNBOOK.md.
    redis_url: str | None = Field(default=None)

    # Tracing collector endpoint (Phoenix). Optional: when unset/empty, tracing is disabled and the
    # app runs untraced. Export is best-effort and must never block startup or requests, so a deploy
    # without a Phoenix collector simply turns tracing off rather than spamming export retries.
    phoenix_collector_endpoint: str | None = Field(default=None)

    # Tracing backend selector: "phoenix" (self-hosted OTLP collector — the default + local dev) or
    # "langsmith" (LangSmith Cloud OTLP ingest — needs NO Railway service, so it sidesteps the host's
    # service cap). Both export through the SAME redacting OTLP exporter, so golden rule #5
    # (redaction-before-export) holds for either destination. See docs/DECISIONS.md.
    tracing_provider: str = Field(default="phoenix")
    # LangSmith Cloud OTLP ingest endpoint + project (non-secret). The API key is a SECRET from Vault
    # (VAULT_KEY_LANGSMITH_API_KEY). Only consulted when tracing_provider == "langsmith".
    langsmith_otlp_endpoint: str = Field(default="https://api.smith.langchain.com/otel")
    langsmith_project: str = Field(default="souschef")

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

    # ── 005-pgadmin-and-openai: provider-agnostic chat/agent LLM seam ─────────────────────────
    # Which hosted provider serves chat/agent GENERATION (NOT embeddings — those stay on their own
    # provider, Decision 7). One config value flips the whole app between Groq (default, current
    # behavior) and OpenAI with zero call-site changes (FR-002). A `Literal` makes an unknown value a
    # fail-fast settings-load error rather than a late surprise (FR-005/SC-003). The two OpenAI knobs
    # mirror the Groq two-model split (workflow `openai_model` vs agent `openai_agent_model`), but both
    # DEFAULT to `gpt-4o-mini` — it is cheap and tool-calling-capable, so it serves the agent fine; raise
    # `openai_agent_model` (e.g. `gpt-4o`) only if you want a stronger model on the multi-tool agent path.
    # Models are pinned non-secret config (Principle V); the OPENAI_API_KEY is a Vault secret, never here.
    llm_provider: Literal["groq", "openai"] = Field(default="groq")
    openai_model: str = Field(default="gpt-4o-mini")
    openai_agent_model: str = Field(default="gpt-4o-mini")

    @property
    def agent_model(self) -> str:
        """The stronger 'agent' model id for the ACTIVE provider — the accessor the bounded agent uses.

        The agent always wants the stronger model, but its *id* is provider-specific, so the call site must
        not name one provider's model (doing so sends e.g. a Groq id to OpenAI → 404 on a swap). Resolving it
        here keeps `app/agent/loop.py` provider-agnostic: `LLM_PROVIDER=openai` routes the agent to
        `openai_agent_model`, Groq (default) to `groq_agent_model`. The workflow path needs no equivalent —
        it passes no model, so each adapter already falls back to its own fast default (`*_model`).
        """
        return self.openai_agent_model if self.llm_provider == "openai" else self.groq_agent_model

    # Bounded-agent limits — the loop terminates when either is hit (Constitution VI / SC-007).
    agent_max_iterations: int = Field(default=5)
    agent_token_budget: int = Field(default=8000)

    # Router escalation: below this classifier confidence, the turn goes to the agent rather than
    # the deterministic workflow (see contracts/classifier.md).
    router_confidence_threshold: float = Field(default=0.55)

    # Vector-search over-fetch size: how many candidates to pull before the allergen wall trims to
    # the 3 displayed cards. Must exceed the display count so wall-compliant cards still surface.
    retrieval_candidate_pool: int = Field(default=20)

    # ── 004-evals-and-uis: operator dashboard login name (non-secret) ─────────────────────────
    # The single operator's username on the Streamlit dashboard. Non-secret, so it comes from env
    # (see .env.example OPERATOR_USERNAME). The matching password hash, cookie-signing key, and
    # shared admin token are SECRETS and come from Vault (see the VAULT_KEY_* constants above).
    operator_username: str = Field(default="operator")

    # ── 004-evals-and-uis: cook-widget CORS allow-list (non-secret) ───────────────────────────
    # The React widget is a browser SPA served from its OWN origin (the Vite dev server on :5173, or
    # the static widget container in compose/Railway) and calls the backend at VITE_API_BASE — a
    # DIFFERENT origin. A browser therefore preflights every request, so the backend must echo the
    # widget origin(s) in its CORS headers or the call is blocked. This is a comma-separated allow-list
    # of permitted origins; defaults cover the Vite dev (5173) and `vite preview` (4173) ports. It is
    # non-secret config (origins, not credentials) and is enforced by CORSMiddleware in main.py. Both the
    # `localhost` and `127.0.0.1` spellings are included because a browser treats them as distinct origins,
    # and opening the dev server on either is common — omitting one blocks the widget with an opaque fetch
    # failure. (In dev the Vite proxy makes calls same-origin anyway; this is the belt to that suspenders.)
    widget_origins: str = Field(
        default=(
            "http://localhost:5173,http://localhost:4173,"
            "http://127.0.0.1:5173,http://127.0.0.1:4173"
        )
    )

    @property
    def widget_origins_list(self) -> list[str]:
        """Parse `widget_origins` into a clean list of origins (drops blanks/whitespace)."""
        return [o.strip() for o in self.widget_origins.split(",") if o.strip()]

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
