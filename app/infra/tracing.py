"""OpenTelemetry tracing wired to a self-hosted Phoenix collector, with redaction-before-export.

Every application request emits a span (see the middleware wired in app/main.py); spans are
batched and shipped to Phoenix over OTLP/HTTP. A wrapping exporter runs core.redaction.redact
over every span attribute BEFORE the batch leaves the process, so no secret value can reach the
trace store (FR-007, trace half). All setup and export is best-effort: a failure to reach or
configure Phoenix degrades to "no tracing" and must never fail a request (Decision 7).
"""

from __future__ import annotations

import contextlib
import copy
from typing import TYPE_CHECKING, cast

import structlog

from app.core.redaction import redact

if TYPE_CHECKING:  # import only for typing — keep runtime import cost off the hot path
    from fastapi import FastAPI
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.trace.export import SpanExporter
    from opentelemetry.trace import Tracer

    from app.config import Settings

log = structlog.get_logger()

# Logical name the backend reports itself as in Phoenix.
_SERVICE_NAME = "souschef-backend"

# OTLP/HTTP exposes traces at this sub-path; the configured endpoint is the Phoenix base URL.
_OTLP_TRACES_PATH = "/v1/traces"


def _traces_endpoint(base: str) -> str:
    """Return the full OTLP/HTTP traces URL from the configured Phoenix base endpoint.

    PHOENIX_COLLECTOR_ENDPOINT is the collector base (e.g. http://phoenix:6006); the OTLP/HTTP
    exporter wants the concrete /v1/traces path. Idempotent if the path is already present.
    """
    trimmed = base.rstrip("/")
    if trimmed.endswith(_OTLP_TRACES_PATH):
        return trimmed
    return trimmed + _OTLP_TRACES_PATH


def _redacted_copy(span: ReadableSpan) -> ReadableSpan:
    """Return a shallow copy of a finished span whose attribute values have been redacted.

    The original span's attributes are an immutable BoundedAttributes mapping, so we copy the
    span and swap in a plain dict of redacted values rather than mutating in place. Each string
    value is passed through redact(); non-string values are carried over unchanged.
    """
    original = span.attributes or {}
    redacted = {
        key: (redact(value) if isinstance(value, str) else value)
        for key, value in original.items()
    }
    clone = copy.copy(span)
    clone._attributes = redacted  # swap the immutable attrs mapping for a redacted plain dict
    return clone


class _RedactingSpanExporter:
    """Wraps a real OTLP exporter and redacts every span's attributes before delegating export.

    This is the single trace-side choke point for FR-007: redaction happens here, synchronously,
    immediately before the bytes go to Phoenix. Export errors are swallowed into a FAILURE result
    so the background batch processor never propagates them toward a request.
    """

    def __init__(self, inner: SpanExporter) -> None:
        """Hold the wrapped exporter that performs the real network export."""
        self._inner = inner

    def export(self, spans):  # type: ignore[no-untyped-def]  # signature mirrors SpanExporter
        """Redact each span's attributes, then delegate to the wrapped exporter."""
        from opentelemetry.sdk.trace.export import SpanExportResult

        redacted = [_redacted_copy(span) for span in spans]
        try:
            return self._inner.export(redacted)
        except Exception:  # export must never raise toward the batch processor
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        """Flush and close the wrapped exporter on shutdown."""
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        """Force-flush the wrapped exporter (used by the batch processor on drain)."""
        return self._inner.force_flush(timeout_millis)


def configure_tracing(settings: Settings) -> Tracer | None:
    """Set up the global tracer provider exporting redacted spans to Phoenix; return a tracer.

    Builds a TracerProvider tagged with the service name + environment, attaches a batch
    processor over the redacting exporter, and installs it globally. Any failure (missing OTLP
    extra, bad config) is logged and yields None so the caller can run untraced — tracing is
    never allowed to break startup or a request (Decision 7).
    """
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create(
            {"service.name": _SERVICE_NAME, "deployment.environment": settings.env}
        )
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=_traces_endpoint(settings.phoenix_collector_endpoint))
        redacting = cast("SpanExporter", _RedactingSpanExporter(exporter))
        provider.add_span_processor(BatchSpanProcessor(redacting))
        trace.set_tracer_provider(provider)
        log.info("tracing.configured", endpoint=settings.phoenix_collector_endpoint)
        return trace.get_tracer(_SERVICE_NAME)
    except Exception as exc:  # never let tracing setup abort startup
        log.warning("tracing.setup_failed", error=str(exc))
        return None


def add_tracing_middleware(app: FastAPI, tracer: Tracer | None) -> None:
    """Register an HTTP middleware that opens one span per application request.

    No-op when tracing failed to configure (tracer is None). The span is named by method+path
    and carries the method, target, and resulting status code. Span creation/attribute calls are
    guarded so a tracing hiccup can never break the request; export happens asynchronously in the
    batch processor, so its failures are already isolated from the request path (Decision 7).
    """
    if tracer is None:
        return

    @app.middleware("http")
    async def _trace_request(request, call_next):  # type: ignore[no-untyped-def]  # ASGI signature
        """Wrap the handler in a span; on any tracing error, serve the request untraced."""
        try:
            span_cm = tracer.start_as_current_span(f"{request.method} {request.url.path}")
        except Exception:  # span machinery unavailable — proceed without a trace
            return await call_next(request)
        with span_cm as span:
            with contextlib.suppress(Exception):  # attribute setting is best-effort
                span.set_attribute("http.method", request.method)
                span.set_attribute("http.target", request.url.path)
            response = await call_next(request)
            with contextlib.suppress(Exception):  # best-effort; never block the response
                span.set_attribute("http.status_code", response.status_code)
            return response
