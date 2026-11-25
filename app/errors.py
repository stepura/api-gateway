"""Domain exceptions. HTTP exception handlers live in app.api.exception_handlers."""

from __future__ import annotations

from typing import Any


class GatewayError(Exception):
    """Base class for errors rendered as the standard response envelope."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        module: str = "system",
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.module = module
        self.details = details


class UnknownModuleError(GatewayError):
    status_code = 404
    code = "unknown_module"


class InvalidParamsError(GatewayError):
    status_code = 422
    code = "invalid_params"


class UpstreamUnavailableError(GatewayError):
    status_code = 502
    code = "upstream_unavailable"


class CacheUnavailableError(GatewayError):
    status_code = 503
    code = "cache_unavailable"


class ConnectorUpstreamError(Exception):
    """Raised by a connector when its upstream failed.

    The query service converts this into either a stale-cache fallback or an
    UpstreamUnavailableError, so it is deliberately not a GatewayError.
    """

    def __init__(
        self,
        module: str,
        message: str,
        *,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.module = module
        self.message = message
        self.details = details
