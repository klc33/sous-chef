"""Phoenix deep-link assembly for the operator dashboard — links out, never rolls up cost.

Phoenix owns trace + per-turn token-cost storage and serves its own rich UI; the dashboard's job is to
*point* at it, not rebuild it (research R5, analyze C2). This module turns the configured Phoenix collector
endpoint into UI deep-links the metrics page renders; when no endpoint is configured (tracing disabled),
it returns None so the page shows an honest "tracing off" state rather than a broken link.
"""

from __future__ import annotations

from app.config import Settings
from app.schemas.admin import PhoenixLinks

# Phoenix serves its UI and its OTLP collector on the same host; the projects view is the trace landing.
_PROJECTS_PATH = "/projects"


def phoenix_links(settings: Settings) -> PhoenixLinks | None:
    """Build the Phoenix UI deep-links from the configured collector endpoint, or None when tracing is off.

    The collector endpoint (`PHOENIX_COLLECTOR_ENDPOINT`) doubles as the Phoenix UI base; we trim any
    trailing slash and append the projects path for a trace/cost deep-link. An unset/empty endpoint means
    this deploy runs untraced, so we return None and the dashboard renders the disabled state — no cost is
    summarized here (it lives in Phoenix).
    """
    base = settings.phoenix_collector_endpoint
    if not base:
        return None
    ui = base.rstrip("/")
    return PhoenixLinks(ui_base_url=ui, trace_deep_link=f"{ui}{_PROJECTS_PATH}")
