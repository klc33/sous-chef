"""OpenAI chat adapter — the alternate hosted LLM behind the workflow reply and the bounded agent.

Built on the **already-vendored** `openai` SDK (the same dependency `infra/embeddings.py` uses), so this
adds NO new runtime dependency (FR-017, SC-009) and no torch. Selected when `settings.llm_provider ==
"openai"`; otherwise dormant. Structurally identical to the Groq adapter (`groq.py`): a lazy `lru_cache`
client reading the API key from Vault (`OPENAI_API_KEY`, never config/code — golden rule #4), the same
`chat(...)` signature, and the same OpenAI-style response object the agent already reads, so the
tool-calling contract and token accounting are identical across providers (Decision 2/4).

Resilience parity: a transient rate-limit (`429`) is retried with bounded backoff honoring the provider's
`retry-after`, mirroring the Groq adapter so a provider swap changes nothing observable under throttling.

Two models mirror the Groq split: `settings.openai_model` (fast/cheap workflow default) and
`settings.openai_agent_model` (stronger, passed by the agent for reliable multi-tool calling).
"""

from __future__ import annotations

import time
from functools import lru_cache
from typing import Any

from openai import OpenAI, RateLimitError

from app.config import get_settings
from app.infra.vault import VaultAdapter

# How many times to retry a throttled (429) call before giving up (mirrors the Groq adapter's budget).
_MAX_RETRIES = 4
# Fallback backoff (seconds) when the provider does not send a usable retry-after header.
_BACKOFF_BASE = 1.0


@lru_cache
def _client() -> OpenAI:
    """Build (once) the OpenAI client, reading the API key from Vault.

    lru_cache caches the Vault read + client across the process, exactly like the Groq and embeddings
    adapters. A missing `OPENAI_API_KEY` raises `StartupConfigError` here on first use (fail-fast), but
    only when OpenAI is the SELECTED provider — the dormant provider's key may stay a placeholder.
    """
    settings = get_settings()
    vault = VaultAdapter(settings)
    vault.load_secrets()
    return OpenAI(api_key=vault.get("OPENAI_API_KEY"))


def _retry_after_seconds(exc: RateLimitError, attempt: int) -> float:
    """Pick how long to wait before retrying a 429: the provider's retry-after, else exponential backoff.

    Honors the `retry-after` header when present (the provider telling us exactly how long to wait);
    otherwise falls back to a simple exponential backoff so repeated throttling does not hot-loop. Mirrors
    the Groq adapter so both providers behave the same under rate limiting.
    """
    header = getattr(getattr(exc, "response", None), "headers", {}) or {}
    raw = header.get("retry-after")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return _BACKOFF_BASE * (2**attempt)


class OpenAIClient:
    """`LLMClient` adapter over the OpenAI chat API (selected when `llm_provider == "openai"`)."""

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> Any:
        """Call OpenAI chat completion (with optional native tool-calling) and return the raw response.

        `model` defaults to the fast workflow model (`settings.openai_model`); the agent passes
        `settings.openai_agent_model`. `tools`, when given, are forwarded with `tool_choice="auto"` so the
        model may emit tool calls (read on `choices[0].message.tool_calls`). `max_tokens` caps the
        per-call output for the agent's token budget. A `429` is retried up to `_MAX_RETRIES` times,
        waiting the provider's `retry-after` (or exponential backoff) between tries; any other error
        propagates. The full OpenAI-style response is returned — the same shape the Groq adapter returns —
        so callers read content, tool calls, and `usage` identically across providers.
        """
        settings = get_settings()
        kwargs: dict[str, Any] = {"model": model or settings.openai_model, "messages": messages}
        if tools is not None:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        last_exc: RateLimitError | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                return _client().chat.completions.create(**kwargs)
            except RateLimitError as exc:  # throttling — wait and retry within budget
                last_exc = exc
                time.sleep(_retry_after_seconds(exc, attempt))
        raise RuntimeError(f"OpenAI rate-limited after {_MAX_RETRIES} retries: {last_exc}")
