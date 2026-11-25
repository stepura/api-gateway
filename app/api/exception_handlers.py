"""Exception handlers that keep the response envelope uniform on errors."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.errors import GatewayError
from app.schemas import ErrorPayload, QueryEnvelope, ResponseSource
from app.utils import validation_error_details

logger = logging.getLogger(__name__)


def _error_response(
    *,
    status_code: int,
    module: str,
    code: str,
    message: str,
    details: Any | None = None,
) -> JSONResponse:
    payload = QueryEnvelope(
        module=module,
        source=ResponseSource.NONE,
        cached_at=None,
        data=ErrorPayload(
            code=code,
            message=message,
            details=jsonable_encoder(details) if details is not None else None,
        ).model_dump(mode="json", exclude_none=True),
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def _module_from_request(request: Request) -> str:
    return str(request.path_params.get("module_name", "system"))


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(GatewayError)
    async def gateway_error_handler(request: Request, exc: GatewayError) -> JSONResponse:
        return _error_response(
            status_code=exc.status_code,
            module=exc.module or _module_from_request(request),
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return _error_response(
            status_code=422,
            module=_module_from_request(request),
            code="validation_error",
            message="Request validation failed",
            details=validation_error_details(exc),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        return _error_response(
            status_code=exc.status_code,
            module=_module_from_request(request),
            code="http_error",
            message=str(exc.detail),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
        return _error_response(
            status_code=500,
            module=_module_from_request(request),
            code="internal_error",
            message="Internal server error",
        )
