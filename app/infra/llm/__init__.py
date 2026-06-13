"""app.infra.llm — the provider-agnostic LLM seam's stable facade (005-pgadmin-and-openai).

This package replaces the former single `app/infra/llm_groq.py` module. Its `__init__` is the facade:
the **one** symbol every caller and test imports — `from app.infra import llm; llm.chat(...)`. `chat(...)`
resolves the active provider via `get_client()` (Groq by default, OpenAI when `llm_provider == "openai"`)
and delegates, so the two call sites (`services/user/rag.py`, `app/agent/loop.py`) and the tests change
only their import. Both adapters return the same OpenAI-style response, so nothing downstream changes.

After a successful call the facade attaches best-effort attributes (`llm.provider`, `llm.model`,
`llm.total_tokens`) to the active OpenTelemetry span so per-turn token/cost attribution is present and
identical under both providers (FR-009a/SC-005a). Because it is set at this single seam, the attribution
is provider-agnostic by construction. All of it is wrapped in `contextlib.suppress` so a tracing hiccup
can never break a turn (Decision 5/7); redaction already runs on span attributes before export, and these
are non-secret values.
"""

from __future__ import annotations

import contextlib
from typing import Any

from app.config import get_settings
from app.infra.llm.factory import get_client

__all__ = ["chat", "get_client"]


def _tag_span(response: Any) -> None:
    """Best-effort: attach provider/model/token attributes to the current span; never raise.

    Reads the resolved model id and cumulative token count off the response (both SDKs expose `.model`
    and `.usage.total_tokens`), and the active provider from settings. Setting attributes on a non-
    recording span (no tracing configured) is a harmless no-op. The whole body is suppressed so a tracing
    failure degrades to "no attribution" rather than failing the generation turn (Decision 5/7).
    """
    with contextlib.suppress(Exception):
        from opentelemetry import trace

        span = trace.get_current_span()
        settings = get_settings()
        usage = getattr(response, "usage", None)
        total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
        span.set_attribute("llm.provider", settings.llm_provider)
        span.set_attribute("llm.model", getattr(response, "model", "") or "")
        span.set_attribute("llm.total_tokens", total_tokens)


def chat(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
) -> Any:
    """Call the active provider's chat completion and return the raw OpenAI-style response.

    The single seam every chat/agent generation flows through: delegates to `get_client().chat(...)`
    (Groq or OpenAI, chosen by `settings.llm_provider`) with the same arguments, then attaches best-effort
    token/provider/model span attributes before returning. Callers read `.choices[0].message.{content,
    tool_calls}` and `.usage.total_tokens` identically regardless of provider.
    """
    response = get_client().chat(messages, tools=tools, max_tokens=max_tokens, model=model)
    _tag_span(response)
    return response
