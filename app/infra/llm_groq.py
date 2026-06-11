"""Groq chat adapter — the hosted LLM behind the workflow reply and the bounded agent.

Groq is chat-only (embeddings live in a separate provider, see `infra/embeddings.py`). This module
exposes one function, `chat(...)`, wrapping Groq's native function/tool-calling API. The API key comes
from Vault (`GROQ_API_KEY`), never config or code (golden rule #4). The client is built lazily and
cached so the Vault read happens once per process; tests monkeypatch `chat` directly and never touch the
network. Two models are used: `settings.groq_model` (fast/cheap, the workflow default) and
`settings.groq_agent_model` (stronger, passed explicitly by the agent for reliable multi-tool calling) —
each model is its own Groq rate-limit bucket.

Free-tier resilience: a `429` is retried with backoff, honoring the provider's `retry-after`, so
throttling surfaces as a brief wait rather than a turn failure.
"""

from __future__ import annotations

import time
from functools import lru_cache
from typing import Any

from groq import Groq, RateLimitError

from app.config import get_settings
from app.infra.vault import VaultAdapter

# How many times to retry a throttled (429) call before giving up.
_MAX_RETRIES = 4
# Fallback backoff (seconds) when the provider does not send a usable retry-after header.
_BACKOFF_BASE = 1.0


@lru_cache
def _client() -> Groq:
    """Build (once) the Groq client, reading the API key from Vault.

    lru_cache caches the Vault read + client across the process. Vault is reachable wherever this runs,
    so a self-contained adapter here avoids threading app state through every caller.
    """
    settings = get_settings()
    vault = VaultAdapter(settings)
    vault.load_secrets()
    return Groq(api_key=vault.get("GROQ_API_KEY"))


def _retry_after_seconds(exc: RateLimitError, attempt: int) -> float:
    """Pick how long to wait before retrying a 429: the provider's retry-after, else exponential backoff.

    Honors the `retry-after` header when present (the provider telling us exactly how long to wait);
    otherwise falls back to a simple exponential backoff so repeated throttling does not hot-loop.
    """
    header = getattr(getattr(exc, "response", None), "headers", {}) or {}
    raw = header.get("retry-after")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return _BACKOFF_BASE * (2**attempt)


def chat(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
) -> Any:
    """Call Groq chat completion (with optional native tool-calling) and return the raw response.

    `model` defaults to the fast workflow model; the agent passes `settings.groq_agent_model`. `tools`,
    when given, are forwarded so the model may emit tool calls (read on `choices[0].message.tool_calls`).
    `max_tokens` caps the per-call output for the agent's token budget. A `429` is retried up to
    `_MAX_RETRIES` times, waiting the provider's `retry-after` (or exponential backoff) between tries; any
    other error propagates. The full response is returned so callers can read the message content,
    tool calls, and `usage` (for the cumulative token bound).
    """
    settings = get_settings()
    kwargs: dict[str, Any] = {"model": model or settings.groq_model, "messages": messages}
    if tools is not None:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    last_exc: RateLimitError | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return _client().chat.completions.create(**kwargs)
        except RateLimitError as exc:  # free-tier throttling — wait and retry within budget
            last_exc = exc
            time.sleep(_retry_after_seconds(exc, attempt))
    raise RuntimeError(f"Groq rate-limited after {_MAX_RETRIES} retries: {last_exc}")
