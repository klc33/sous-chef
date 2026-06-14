"""Unit tests for tracing backend selection (app/infra/tracing._exporter_config).

Pins the OTLP endpoint + auth-header resolution for both backends and the best-effort disable paths,
without standing up an exporter: Phoenix (self-hosted, no auth), LangSmith Cloud (auth headers, requires
the Vault key), and the "tracing off" cases (no endpoint / LangSmith with no key). A SimpleNamespace
stands in for Settings — the helper only reads four plain attributes.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.infra.tracing import _exporter_config


def _settings(**over: object) -> SimpleNamespace:
    """Build a Settings stand-in with tracing defaults, overridable per test."""
    base = {
        "tracing_provider": "phoenix",
        "phoenix_collector_endpoint": "http://phoenix:6006",
        "langsmith_otlp_endpoint": "https://api.smith.langchain.com/otel",
        "langsmith_project": "souschef",
    }
    base.update(over)
    return SimpleNamespace(**base)


def test_phoenix_endpoint_appends_traces_path_and_no_headers() -> None:
    """Phoenix: the collector base gets /v1/traces and there are no auth headers."""
    cfg = _exporter_config(_settings(tracing_provider="phoenix"), api_key=None)
    assert cfg == ("http://phoenix:6006/v1/traces", {})


def test_phoenix_disabled_when_endpoint_unset() -> None:
    """Phoenix with no collector endpoint disables tracing (None), not a broken exporter."""
    cfg = _exporter_config(_settings(tracing_provider="phoenix", phoenix_collector_endpoint=None), None)
    assert cfg is None


def test_langsmith_builds_endpoint_and_auth_headers() -> None:
    """LangSmith: OTLP base + /v1/traces, with x-api-key + project headers from the Vault key."""
    cfg = _exporter_config(_settings(tracing_provider="langsmith"), api_key="ls-secret")
    assert cfg is not None
    endpoint, headers = cfg
    assert endpoint == "https://api.smith.langchain.com/otel/v1/traces"
    assert headers == {"x-api-key": "ls-secret", "Langsmith-Project": "souschef"}


def test_langsmith_disabled_without_api_key() -> None:
    """LangSmith selected but no Vault key → tracing off (best-effort), never a startup failure."""
    assert _exporter_config(_settings(tracing_provider="langsmith"), api_key=None) is None


def test_provider_is_case_insensitive() -> None:
    """The provider selector is matched case-insensitively."""
    cfg = _exporter_config(_settings(tracing_provider="LangSmith"), api_key="k")
    assert cfg is not None and cfg[1]["x-api-key"] == "k"
