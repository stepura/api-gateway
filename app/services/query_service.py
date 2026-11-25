"""Orchestration: connector execution, cache policy and stale fallback."""

from __future__ import annotations

import asyncio
import logging
import weakref
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx

from app.config import Settings
from app.connectors.base import BaseConnector
from app.errors import (
    CacheUnavailableError,
    ConnectorUpstreamError,
    UnknownModuleError,
    UpstreamUnavailableError,
)
from app.schemas import QueryEnvelope, ResponseSource
from app.services.cache_service import CacheEntry, CacheService
from app.utils import iso_utc

logger = logging.getLogger(__name__)


class QueryService:
    """The only component that knows both connectors and the cache.

    Concurrent refreshes of the same cache key are collapsed with a per-key
    asyncio lock. Locks are process-local, which is why the container runs one
    uvicorn worker; a multi-worker deployment would need a distributed lock.
    """

    def __init__(
        self,
        settings: Settings,
        cache: CacheService,
        connectors: Mapping[str, BaseConnector],
    ) -> None:
        self._settings = settings
        self._cache = cache
        self._connectors = dict(connectors)
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    def describe_modules(self) -> list[dict[str, Any]]:
        return [self._connectors[name].describe() for name in sorted(self._connectors)]

    async def execute(
        self,
        module_name: str,
        raw_params: Mapping[str, Any],
        *,
        force_refresh: bool = False,
        simulate_outage: bool = False,
    ) -> QueryEnvelope:
        connector = self._get_connector(module_name)
        params = connector.prepare_params(raw_params)
        key = self._cache.build_key(module_name, params)
        simulate_active = simulate_outage and self._settings.enable_failure_simulation

        entry = await self._read_cache(module_name, params)
        if entry is not None and entry.is_fresh and not force_refresh and not simulate_active:
            return self._envelope(
                module_name,
                ResponseSource.CACHE,
                entry.data,
                iso_utc(entry.cached_at),
            )

        async with self._key_lock(key):
            # Another request may have refreshed the entry while we waited.
            entry = await self._read_cache(module_name, params)
            if entry is not None and entry.is_fresh and not force_refresh and not simulate_active:
                return self._envelope(
                    module_name,
                    ResponseSource.CACHE,
                    entry.data,
                    iso_utc(entry.cached_at),
                )

            try:
                data = await self._fetch_live(connector, params, simulate_active)
            except (TimeoutError, ConnectorUpstreamError, httpx.HTTPError, OSError) as exc:
                reason = (
                    exc.message
                    if isinstance(exc, ConnectorUpstreamError)
                    else str(exc)
                )
                logger.warning("Live fetch failed for module=%s: %s", module_name, reason)
                if entry is not None and self._settings.stale_cache_fallback_enabled:
                    logger.warning("Serving fallback cache for module=%s", module_name)
                    return self._envelope(
                        module_name,
                        ResponseSource.STALE_CACHE,
                        entry.data,
                        iso_utc(entry.cached_at),
                    )
                raise UpstreamUnavailableError(
                    f"Module '{module_name}' is unavailable and no cached response can be served",
                    module=module_name,
                    details={"reason": reason, "cache_available": False},
                ) from exc

            cached_at = await self._write_cache(module_name, params, data, connector)
            return self._envelope(module_name, ResponseSource.LIVE, data, cached_at)

    async def invalidate(
        self,
        module_name: str,
        raw_params: Mapping[str, Any] | None = None,
    ) -> QueryEnvelope:
        """Delete one cache key, or all keys of a module when params are omitted."""
        connector = self._get_connector(module_name)
        params = connector.prepare_params(raw_params) if raw_params else None
        try:
            deleted = (
                await self._cache.delete(module_name, params)
                if params is not None
                else await self._cache.delete_module(module_name)
            )
        except CacheUnavailableError as exc:
            raise CacheUnavailableError(
                f"Cache invalidation failed: {exc.message}",
                module=module_name,
                details=exc.details,
            ) from exc

        return QueryEnvelope(
            module="cache",
            source=ResponseSource.LIVE,
            cached_at=None,
            data={
                "module": module_name,
                "scope": "key" if params is not None else "module",
                "deleted": deleted,
            },
        )

    def _get_connector(self, module_name: str) -> BaseConnector:
        connector = self._connectors.get(module_name)
        if connector is None:
            raise UnknownModuleError(
                f"Unknown module '{module_name}'",
                module=module_name,
                details={"available_modules": sorted(self._connectors)},
            )
        return connector

    async def _fetch_live(
        self,
        connector: BaseConnector,
        params: Mapping[str, Any],
        simulate_active: bool,
    ) -> Any:
        if simulate_active:
            raise ConnectorUpstreamError(
                connector.name,
                "Upstream outage simulated by request",
                details={"simulate_outage": True},
            )
        try:
            return await connector.fetch(params)
        except TimeoutError as exc:
            raise ConnectorUpstreamError(
                connector.name,
                f"Upstream timed out: {exc}",
            ) from exc
        except httpx.HTTPError as exc:
            raise ConnectorUpstreamError(
                connector.name,
                f"Upstream HTTP error: {exc}",
            ) from exc
        except OSError as exc:
            raise ConnectorUpstreamError(
                connector.name,
                f"Upstream network error: {exc}",
            ) from exc

    async def _read_cache(
        self,
        module_name: str,
        params: Mapping[str, Any],
    ) -> CacheEntry | None:
        try:
            return await self._cache.get(module_name, params)
        except CacheUnavailableError as exc:
            logger.warning("Cache read unavailable for module=%s: %s", module_name, exc.message)
            return None

    async def _write_cache(
        self,
        module_name: str,
        params: Mapping[str, Any],
        data: Any,
        connector: BaseConnector,
    ) -> str | None:
        try:
            entry = await self._cache.set(
                module_name,
                params,
                data,
                connector.cache_ttl_seconds,
            )
            return iso_utc(entry.cached_at)
        except CacheUnavailableError as exc:
            # A cache outage must not turn a successful upstream call into an error.
            logger.warning("Cache write unavailable for module=%s: %s", module_name, exc.message)
            return None

    @asynccontextmanager
    async def _key_lock(self, key: str) -> AsyncIterator[None]:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        # The local variable keeps the weakly referenced lock alive for the
        # whole critical section; unused locks are garbage-collected afterwards.
        async with lock:
            yield

    @staticmethod
    def _envelope(
        module: str,
        source: ResponseSource,
        data: Any,
        cached_at: str | None = None,
    ) -> QueryEnvelope:
        return QueryEnvelope(
            module=module,
            source=source,
            cached_at=cached_at,
            data=data,
        )
