"""app.infra.llm — the provider-agnostic LLM seam's stable facade (005-pgadmin-and-openai).

This package replaces the former single `app/infra/llm_groq.py` module. Its `__init__` is the facade:
the **one** symbol every caller and test imports — `from app.infra import llm; llm.chat(...)`. `chat(...)`
resolves the active provider via `get_client()` (Groq by default, OpenAI when `llm_provider == "openai"`)
and delegates, so the two call sites (`services/user/rag.py`, `app/agent/loop.py`) and the tests change
only their import. Both adapters return the same OpenAI-style response, so nothing downstream changes.

Each generation runs inside a dedicated **child span** named per the OpenTelemetry **GenAI semantic
conventions** (`chat {model}`) so the trace store records a proper `llm`-type run with token usage (and,
once model pricing is configured, cost) — not just one generic per-request `chain` span (T017j). The span
carries `gen_ai.system` / `gen_ai.request.model` and the usage attributes `gen_ai.usage.input_tokens` /
`gen_ai.usage.output_tokens` (read off the `usage` both SDKs return); LangSmith's OTLP ingest maps these
to prompt/completion tokens. The legacy `llm.provider` / `llm.model` / `llm.total_tokens` attributes are
retained on the same span (FR-009a/SC-005a). Because the span lives at this single seam, the attribution
is provider-agnostic by construction. All of it is best-effort (Decision 5/7): a tracing hiccup degrades
to "no attribution" but never breaks a turn, and a no-op tracer (tracing disabled) is a harmless no-op.
Redaction already runs on span attributes before export and these are non-secret values (golden rule #5).
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from app.config import get_settings
from app.infra.llm.factory import get_client

if TYPE_CHECKING:  # typing-only import — keep the OTel import off the module-load path
    from opentelemetry.trace import Span

__all__ = ["chat", "get_client"]


def _request_model(model: str | None) -> str:
    """Resolve the model id the adapter will use, so the span name/attrs match the real request.

    The facade may be called with `model=None` (the workflow's fast default) or an explicit model (the
    agent passes its stronger model). Mirror the adapter's own defaulting — OpenAI vs Groq fast model —
    so `gen_ai.request.model` and the `chat {model}` span name reflect what actually gets sent.
    """
    if model:
        return model
    settings = get_settings()
    return settings.openai_model if settings.llm_provider == "openai" else settings.groq_model


@contextlib.contextmanager
def _llm_span(model: str | None) -> Iterator[Span | None]:
    """Open a GenAI-convention child span for one chat call; yield it (or None) best-effort.

    Names the span `chat {model}` and sets the request-side GenAI attributes (`gen_ai.system`,
    `gen_ai.request.model`, `gen_ai.operation.name`) so the trace backend classifies it as an `llm` run.
    Every tracing call is guarded: if the OTel machinery is unavailable or tracing is disabled, this
    yields None (or a no-op span) and the generation proceeds untraced — tracing must never break a turn
    (Decision 5/7). The span is always closed in the `finally`, even when the wrapped call raises.
    """
    span = None
    cm = None
    try:
        from opentelemetry import trace

        settings = get_settings()
        model_name = _request_model(model)
        system = "openai" if settings.llm_provider == "openai" else "groq"
        cm = trace.get_tracer(__name__).start_as_current_span(f"chat {model_name}")
        span = cm.__enter__()
        with contextlib.suppress(Exception):  # attribute setting is best-effort
            span.set_attribute("gen_ai.system", system)
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.request.model", model_name)
    except Exception:  # span machinery unavailable — run untraced rather than failing the turn
        span, cm = None, None
    try:
        yield span
    finally:
        if cm is not None:
            with contextlib.suppress(Exception):  # closing the span must never raise toward the caller
                cm.__exit__(None, None, None)


def _record_usage(span: Span | None, response: Any) -> None:
    """Best-effort: record GenAI token-usage (+ legacy llm.*) attributes onto the LLM span; never raise.

    Reads `usage.{prompt_tokens, completion_tokens, total_tokens}` and the resolved `.model` off the
    response (both SDKs expose them) and writes the OTel GenAI usage attributes so LangSmith/Phoenix show
    token counts (and cost once priced). Also keeps the prior provider-agnostic `llm.*` attributes for
    continuity (FR-009a/SC-005a). Suppressed wholesale so a tracing failure degrades to "no attribution"
    rather than failing the generation turn (Decision 5/7).
    """
    if span is None:
        return
    with contextlib.suppress(Exception):
        settings = get_settings()
        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
        response_model = getattr(response, "model", "") or ""
        # OTel GenAI usage attributes — LangSmith's OTLP ingest maps these to prompt/completion tokens.
        span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
        span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
        if response_model:
            span.set_attribute("gen_ai.response.model", response_model)
        # Legacy provider-agnostic attribution, retained for continuity (FR-009a/SC-005a).
        span.set_attribute("llm.provider", settings.llm_provider)
        span.set_attribute("llm.model", response_model)
        span.set_attribute("llm.total_tokens", total_tokens)


def chat(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
) -> Any:
    """Call the active provider's chat completion and return the raw OpenAI-style response.

    The single seam every chat/agent generation flows through: opens a GenAI-convention `llm` child span,
    delegates to `get_client().chat(...)` (Groq or OpenAI, chosen by `settings.llm_provider`) with the
    same arguments, records token-usage attributes on the span, then returns. Callers read
    `.choices[0].message.{content, tool_calls}` and `.usage.total_tokens` identically regardless of
    provider; the span work is best-effort and never alters the returned response.
    """
    with _llm_span(model) as span:
        response = get_client().chat(messages, tools=tools, max_tokens=max_tokens, model=model)
        _record_usage(span, response)
        return response
