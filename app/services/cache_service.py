"""Deterministic response cache backed by MongoDB TTL indexes."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from bson.errors import BSONError
from motor.motor_asyncio import AsyncIOMotorCollection
from pymongo.errors import PyMongoError

from app.config import Settings
from app.errors import CacheUnavailableError
from app.utils import (
    canonical_json,
    canonicalize_json,
    iso_utc,
    to_aware_utc,
    to_naive_utc,
    utc_now,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CacheEntry:
    key: str
    module: str
    params: dict[str, Any]
    data: Any
    cached_at: datetime  # timezone-aware UTC
    expires_at: datetime  # timezone-aware UTC
    purge_at: datetime  # timezone-aware UTC
    ttl_seconds: int

    @property
    def is_fresh(self) -> bool:
        return self.expires_at > utc_now()

    @classmethod
    def from_document(cls, key: str, document: Mapping[str, Any]) -> CacheEntry:
        required = (
            "module",
            "params",
            "data",
            "cached_at",
            "expires_at",
            "purge_at",
            "ttl_seconds",
        )
        missing = [field for field in required if field not in document]
        if missing:
            raise ValueError(f"cache document is missing fields: {missing}")
        return cls(
            key=key,
            module=str(document["module"]),
            params=dict(document["params"]),
            data=document["data"],
            cached_at=to_aware_utc(document["cached_at"]),
            expires_at=to_aware_utc(document["expires_at"]),
            purge_at=to_aware_utc(document["purge_at"]),
            ttl_seconds=int(document["ttl_seconds"]),
        )


class CacheService:
    """Read/write cache documents. All failures are converted to domain errors."""

    def __init__(self, collection: AsyncIOMotorCollection, settings: Settings) -> None:
        self._collection = collection
        self._settings = settings

    @staticmethod
    def build_key(module_name: str, params: Mapping[str, Any]) -> str:
        """SHA-256 of the module name plus canonicalized, sorted JSON params."""
        payload = f"{module_name}:{canonical_json(params)}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def get(self, module_name: str, params: Mapping[str, Any]) -> CacheEntry | None:
        key = self.build_key(module_name, params)
        try:
            document = await self._collection.find_one({"_id": key, "module": module_name})
        except (PyMongoError, BSONError, TypeError, ValueError) as exc:
            raise CacheUnavailableError(
                "Cache read failed",
                module=module_name,
                details={"operation": "find_one", "reason": str(exc)},
            ) from exc

        if document is None:
            return None
        try:
            return CacheEntry.from_document(key, document)
        except (TypeError, ValueError, KeyError) as exc:
            logger.warning("Ignoring malformed cache document %s: %s", key, exc)
            return None

    async def set(
        self,
        module_name: str,
        params: Mapping[str, Any],
        data: Any,
        ttl_seconds: int,
    ) -> CacheEntry:
        """Upsert a cache snapshot, returning the stored entry."""
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be >= 1")

        now = utc_now()
        now_naive = to_naive_utc(now)
        expires_naive = now_naive + timedelta(seconds=ttl_seconds)
        purge_naive = expires_naive + timedelta(
            seconds=self._settings.stale_cache_retention_seconds
        )
        key = self.build_key(module_name, params)
        document: dict[str, Any] = {
            "_id": key,
            "module": module_name,
            "params": canonicalize_json(dict(params)),
            "data": data,
            "cached_at": now_naive,
            "expires_at": expires_naive,
            "purge_at": purge_naive,
            "ttl_seconds": ttl_seconds,
            "schema_version": 1,
        }

        try:
            await self._collection.replace_one({"_id": key}, document, upsert=True)
        except (PyMongoError, BSONError, TypeError, ValueError) as exc:
            raise CacheUnavailableError(
                "Cache write failed",
                module=module_name,
                details={"operation": "replace_one", "reason": str(exc)},
            ) from exc

        logger.debug("Cached module=%s key=%s ttl=%ss", module_name, key, ttl_seconds)
        return CacheEntry(
            key=key,
            module=module_name,
            params=cast(dict[str, Any], canonicalize_json(dict(params))),
            data=data,
            cached_at=now,
            expires_at=to_aware_utc(expires_naive),
            purge_at=to_aware_utc(purge_naive),
            ttl_seconds=ttl_seconds,
        )

    async def delete(self, module_name: str, params: Mapping[str, Any]) -> int:
        key = self.build_key(module_name, params)
        try:
            result = await self._collection.delete_one({"_id": key, "module": module_name})
        except (PyMongoError, BSONError, TypeError, ValueError) as exc:
            raise CacheUnavailableError(
                "Cache deletion failed",
                module=module_name,
                details={"operation": "delete_one", "reason": str(exc)},
            ) from exc
        return int(result.deleted_count)

    async def delete_module(self, module_name: str) -> int:
        try:
            result = await self._collection.delete_many({"module": module_name})
        except (PyMongoError, BSONError, TypeError, ValueError) as exc:
            raise CacheUnavailableError(
                "Cache deletion failed",
                module=module_name,
                details={"operation": "delete_many", "reason": str(exc)},
            ) from exc
        return int(result.deleted_count)

    @staticmethod
    def cached_at_iso(entry: CacheEntry) -> str | None:
        return iso_utc(entry.cached_at)
