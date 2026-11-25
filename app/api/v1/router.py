"""Version 1 router aggregation."""

from fastapi import APIRouter

from app.api.v1 import cache, query, system

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(system.router)
api_router.include_router(query.router)
api_router.include_router(cache.router)
