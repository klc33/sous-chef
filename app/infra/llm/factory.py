"""Provider selection — `get_client()` returns the active `LLMClient` by `settings.llm_provider`.

The one place the chat/agent provider choice is resolved. `settings.llm_provider` is a `Literal`, so an
unknown value can never reach here — it fails at settings load (FR-005/SC-003). The chosen client is
cached per process (`lru_cache`) so the adapter (and its lazily-built SDK client) is constructed once.
Switching providers is therefore a startup-time choice, not runtime auto-failover (out of scope).
"""

from __future__ import annotations

from functools import lru_cache

from app.config import get_settings
from app.infra.llm.base import LLMClient
from app.infra.llm.groq import GroqClient
from app.infra.llm.openai import OpenAIClient


@lru_cache
def get_client() -> LLMClient:
    """Return the active chat/agent `LLMClient`, selected by `settings.llm_provider` and cached.

    `groq` (the default) → `GroqClient`; `openai` → `OpenAIClient`. The `Literal` type on
    `llm_provider` guarantees one of these two, so no error branch is needed here — a bad value already
    failed at settings construction. Cached so the provider (and its Vault-read SDK client) is built once.
    """
    if get_settings().llm_provider == "openai":
        return OpenAIClient()
    return GroqClient()
