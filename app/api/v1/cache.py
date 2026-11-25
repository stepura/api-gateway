"""Operational cache invalidation endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends

from app.api.deps import get_query_service
from app.schemas import QueryEnvelope, QueryRequest
from app.services.query_service import QueryService

router = APIRouter(prefix="/cache", tags=["cache"])

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": QueryEnvelope, "description": "Unknown module"},
    422: {"model": QueryEnvelope, "description": "Invalid parameters"},
    503: {"model": QueryEnvelope, "description": "Cache unavailable"},
}


@router.delete(
    "/{module_name}",
    response_model=QueryEnvelope,
    responses=ERROR_RESPONSES,
    summary="Invalidate one cache key or all keys of a module",
)
async def invalidate_cache(
    module_name: str,
    payload: QueryRequest | None = Body(
        default=None,
        description="Provide params to delete one key; omit the body to delete all module keys",
    ),
    service: QueryService = Depends(get_query_service),
) -> QueryEnvelope:
    params = payload.params if payload is not None and payload.params else None
    return await service.invalidate(module_name, params)
