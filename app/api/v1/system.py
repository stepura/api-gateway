"""Health, readiness and connector discovery endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.api.deps import get_container, get_query_service, get_settings_dep
from app.config import Settings
from app.schemas import QueryEnvelope, ResponseSource
from app.services.query_service import QueryService
from app.utils import iso_utc, utc_now

router = APIRouter(tags=["system"])

HEALTH_RESPONSES: dict[int | str, dict[str, Any]] = {
    503: {"model": QueryEnvelope, "description": "MongoDB is unavailable"},
}


@router.get(
    "/health",
    response_model=QueryEnvelope,
    responses=HEALTH_RESPONSES,
    summary="Liveness/readiness probe including MongoDB status",
)
async def health(request: Request) -> JSONResponse:
    container = get_container(request)
    mongo_ok = await container.mongodb.ping()
    envelope = QueryEnvelope(
        module="system",
        source=ResponseSource.LIVE,
        cached_at=None,
        data={
            "status": "ok" if mongo_ok else "degraded",
            "mongo": "up" if mongo_ok else "down",
            "app": container.settings.app_name,
            "environment": container.settings.app_env,
            "version": __version__,
            "checked_at": iso_utc(utc_now()),
        },
    )
    return JSONResponse(
        status_code=200 if mongo_ok else 503,
        content=envelope.model_dump(mode="json"),
    )


@router.get(
    "/modules",
    response_model=QueryEnvelope,
    summary="List auto-discovered connectors and their cache policies",
)
async def list_modules(
    settings: Settings = Depends(get_settings_dep),
    service: QueryService = Depends(get_query_service),
) -> QueryEnvelope:
    modules = service.describe_modules()
    return QueryEnvelope(
        module="registry",
        source=ResponseSource.LIVE,
        cached_at=None,
        data={
            "count": len(modules),
            "default_cache_ttl_seconds": settings.default_cache_ttl_seconds,
            "modules": modules,
        },
    )
