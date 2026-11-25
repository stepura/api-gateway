"""Dependency container: owns the lifetime of all process-wide resources."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app import __version__
from app.config import Settings
from app.connectors import discover_connectors, get_registry
from app.connectors.base import BaseConnector
from app.database import MongoDB
from app.services.cache_service import CacheService
from app.services.query_service import QueryService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AppContainer:
    settings: Settings
    mongodb: MongoDB
    http_client: httpx.AsyncClient
    connectors: dict[str, BaseConnector]
    cache_service: CacheService
    query_service: QueryService

    @classmethod
    async def create(cls, settings: Settings) -> AppContainer:
        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                settings.http_timeout_seconds,
                connect=settings.http_connect_timeout_seconds,
            ),
            limits=httpx.Limits(
                max_connections=settings.http_max_connections,
                max_keepalive_connections=settings.http_max_keepalive_connections,
            ),
            headers={
                "Accept": "application/json",
                "User-Agent": f"{settings.app_name}/{__version__}",
            },
            follow_redirects=True,
        )
        mongodb = MongoDB(settings)

        try:
            await mongodb.connect()
            modules = discover_connectors()
            connectors = get_registry().create_all(settings, http_client)
            if not connectors:
                raise RuntimeError("No connectors were registered; check app/connectors/")
            logger.info("Discovered connector modules: %s", ", ".join(modules) or "(none)")

            cache_service = CacheService(mongodb.collection, settings)
            query_service = QueryService(settings, cache_service, connectors)
            return cls(
                settings=settings,
                mongodb=mongodb,
                http_client=http_client,
                connectors=connectors,
                cache_service=cache_service,
                query_service=query_service,
            )
        except Exception:
            await http_client.aclose()
            mongodb.close()
            raise

    async def aclose(self) -> None:
        await self.http_client.aclose()
        self.mongodb.close()
