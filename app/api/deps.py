"""FastAPI dependency providers backed by the application container."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:
    from app.config import Settings
    from app.container import AppContainer
    from app.database import MongoDB
    from app.services.cache_service import CacheService
    from app.services.query_service import QueryService


def get_container(request: Request) -> AppContainer:
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise RuntimeError("Application container is not initialized")
    return container


def get_settings_dep(request: Request) -> Settings:
    return get_container(request).settings


def get_mongodb(request: Request) -> MongoDB:
    return get_container(request).mongodb


def get_cache_service(request: Request) -> CacheService:
    return get_container(request).cache_service


def get_query_service(request: Request) -> QueryService:
    return get_container(request).query_service
