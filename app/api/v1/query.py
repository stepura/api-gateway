"""Single data endpoint for every registered connector."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request

from app.api.deps import get_query_service
from app.schemas import QueryEnvelope, QueryRequest
from app.services.query_service import QueryService

router = APIRouter(prefix="/query", tags=["query"])

RESERVED_QUERY_PARAMS = frozenset({"force_refresh", "simulate_outage"})
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": QueryEnvelope, "description": "Unknown module"},
    422: {"model": QueryEnvelope, "description": "Invalid parameters"},
    502: {"model": QueryEnvelope, "description": "Upstream unavailable, no cache"},
    503: {"model": QueryEnvelope, "description": "Cache unavailable"},
}


def _params_from_query(request: Request) -> dict[str, Any]:
    """Forward arbitrary query parameters to a connector.

    Repeated keys become lists; control flags are excluded so they never alter
    the deterministic cache key.
    """
    params: dict[str, Any] = {}
    for key, value in request.query_params.multi_items():
        if key in RESERVED_QUERY_PARAMS:
            continue
        if key not in params:
            params[key] = value
        elif isinstance(params[key], list):
            params[key].append(value)
        else:
            params[key] = [params[key], value]
    return params


@router.get(
    "/{module_name}",
    response_model=QueryEnvelope,
    responses=ERROR_RESPONSES,
    summary="Query a connector using query-string parameters",
)
async def query_get(
    module_name: str,
    request: Request,
    force_refresh: bool = Query(
        default=False,
        description="Bypass a fresh cache entry and refresh from the upstream",
    ),
    simulate_outage: bool = Query(
        default=False,
        description="Debug-only: emulate an upstream outage to test stale fallback",
    ),
    service: QueryService = Depends(get_query_service),
) -> QueryEnvelope:
    return await service.execute(
        module_name,
        _params_from_query(request),
        force_refresh=force_refresh,
        simulate_outage=simulate_outage,
    )


@router.post(
    "/{module_name}",
    response_model=QueryEnvelope,
    responses=ERROR_RESPONSES,
    summary="Query a connector using a JSON body with arbitrary parameters",
)
async def query_post(
    module_name: str,
    payload: QueryRequest | None = Body(
        default=None,
        description='Optional body, e.g. {"params": {"tenant_id": "acme", "limit": 3}}',
    ),
    force_refresh: bool = Query(
        default=False,
        description="Bypass a fresh cache entry and refresh from the upstream",
    ),
    simulate_outage: bool = Query(
        default=False,
        description="Debug-only: emulate an upstream outage to test stale fallback",
    ),
    service: QueryService = Depends(get_query_service),
) -> QueryEnvelope:
    params = payload.params if payload is not None else {}
    return await service.execute(
        module_name,
        params,
        force_refresh=force_refresh,
        simulate_outage=simulate_outage,
    )
