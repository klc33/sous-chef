"""Unit tests for the LLM-span instrumentation in the `app.infra.llm` facade (T017j).

Pins the GenAI-convention child span the facade opens around each chat call: a real in-memory
TracerProvider captures the finished span, and a fake LLM client returns a canned `usage`, so the test
asserts the span name + GenAI usage attributes with **no network and no Vault**. This is the regression
guard that traces carry token usage (LangSmith maps `gen_ai.usage.*` → prompt/completion tokens), so a
future change that drops back to a single per-request `chain` span (the pre-T017j state) fails here.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from app.infra import llm
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


class _FakeClient:
    """Stands in for the resolved provider client: `.chat(...)` returns a canned usage-bearing response."""

    def chat(self, *_args: Any, **_kwargs: Any) -> Any:
        """Return an OpenAI-style response carrying prompt/completion/total token usage."""
        return SimpleNamespace(
            model="llama-3.1-8b-instant",
            choices=[SimpleNamespace(message=SimpleNamespace(content="hi", tool_calls=None))],
            usage=SimpleNamespace(prompt_tokens=30, completion_tokens=12, total_tokens=42),
        )


@pytest.fixture
def captured_spans(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    """Route the facade's tracer to an in-memory exporter and stub the provider client.

    Builds a standalone TracerProvider with a synchronous (Simple) processor so finished spans are
    available immediately, and patches `trace.get_tracer` so the facade records onto it without touching
    the global provider. Also swaps `get_client` for a fake so no Vault read / network call happens.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer", lambda *a, **k: provider.get_tracer("test"))
    monkeypatch.setattr(llm, "get_client", lambda: _FakeClient())
    # Pin the provider/models so the assertions don't depend on the ambient LLM_PROVIDER config.
    settings = SimpleNamespace(
        llm_provider="groq", groq_model="llama-3.1-8b-instant", openai_model="gpt-4o-mini"
    )
    monkeypatch.setattr(llm, "get_settings", lambda: settings)
    return exporter


def test_chat_emits_llm_span_with_usage_attributes(captured_spans: InMemorySpanExporter) -> None:
    """A chat call emits one GenAI-convention span carrying the token-usage attributes (T017j)."""
    response = llm.chat([{"role": "user", "content": "hello"}], model="llama-3.1-8b-instant")

    # The response is returned untouched — span work never alters it.
    assert response.usage.total_tokens == 42

    spans = captured_spans.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "chat llama-3.1-8b-instant"

    attrs = dict(span.attributes or {})
    # OTel GenAI semantic-convention attributes — what LangSmith maps to an `llm` run with token usage.
    assert attrs["gen_ai.system"] == "groq"
    assert attrs["gen_ai.operation.name"] == "chat"
    assert attrs["gen_ai.request.model"] == "llama-3.1-8b-instant"
    assert attrs["gen_ai.usage.input_tokens"] == 30
    assert attrs["gen_ai.usage.output_tokens"] == 12
    # Legacy provider-agnostic attribution retained for continuity (FR-009a/SC-005a).
    assert attrs["llm.provider"] == "groq"
    assert attrs["llm.total_tokens"] == 42


def test_chat_span_uses_workflow_default_model_when_unspecified(
    captured_spans: InMemorySpanExporter,
) -> None:
    """With no explicit model, the span name/attrs reflect the resolved fast workflow model."""
    llm.chat([{"role": "user", "content": "hello"}])

    span = captured_spans.get_finished_spans()[0]
    # Groq is the default provider → the fast workflow model (config default).
    assert span.attributes["gen_ai.request.model"] == "llama-3.1-8b-instant"
    assert span.name == "chat llama-3.1-8b-instant"
