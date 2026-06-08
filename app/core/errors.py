"""Application error types and FastAPI exception handlers.

Gives the app a small, explicit error vocabulary and clean JSON responses, plus a dedicated
startup-configuration error used to fail fast when the app cannot be built (FR-010). Handlers
never echo internal exception detail to clients.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

log = structlog.get_logger()


class AppError(Exception):
    """Base class for expected application errors carrying an HTTP status + machine code."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        """Store the human message and allow per-instance overrides of status/code."""
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code


class StartupConfigError(AppError):
    """Raised when the app cannot start: missing/invalid config or an unreachable critical
    dependency at boot. Meant to abort the process, not to be returned as a request response."""

    status_code = 500
    code = "startup_config_error"


async def _app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render a known AppError as a clean JSON body with its status code and machine code."""
    assert isinstance(exc, AppError)  # registered only for AppError
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all: log the real error server-side, return a generic 500 with no internals leaked."""
    log.error("unhandled_error", error=str(exc), path=str(request.url.path))
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": "Internal server error"}},
    )


def register_error_handlers(app: FastAPI) -> None:
    """Wire both handlers onto the FastAPI app (call once in the app factory)."""
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
