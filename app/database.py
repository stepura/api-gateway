"""MongoDB connection lifecycle and index bootstrap."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import OperationFailure, PyMongoError

from app.config import Settings

logger = logging.getLogger(__name__)


class MongoDB:
    TTL_INDEX_NAME = "cache_purge_at_ttl"
    MODULE_INDEX_NAME = "cache_module_cached_at"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: AsyncIOMotorClient | None = None
        self._collection: AsyncIOMotorCollection | None = None

    @property
    def client(self) -> AsyncIOMotorClient:
        if self._client is None:
            raise RuntimeError("MongoDB client is not connected")
        return self._client

    @property
    def collection(self) -> AsyncIOMotorCollection:
        if self._collection is None:
            raise RuntimeError("MongoDB collection is not initialized")
        return self._collection

    async def connect(self) -> None:
        """Create the client, wait for MongoDB and ensure all cache indexes."""
        self._client = AsyncIOMotorClient(
            self._settings.mongodb_uri,
            serverSelectionTimeoutMS=self._settings.mongo_server_selection_timeout_ms,
        )
        database: Any = self._client[self._settings.mongodb_database]
        self._collection = database[self._settings.mongodb_cache_collection]

        await self._wait_until_ready()
        await self.ensure_indexes()
        logger.info(
            "MongoDB connected: database=%s collection=%s",
            self._settings.mongodb_database,
            self._settings.mongodb_cache_collection,
        )

    async def _wait_until_ready(self) -> None:
        last_error: PyMongoError | None = None
        for attempt in range(1, self._settings.mongo_startup_attempts + 1):
            try:
                await self.client.admin.command("ping")
                return
            except PyMongoError as exc:
                last_error = exc
                logger.warning(
                    "MongoDB is not ready (attempt %s/%s): %s",
                    attempt,
                    self._settings.mongo_startup_attempts,
                    exc,
                )
                if attempt < self._settings.mongo_startup_attempts:
                    await asyncio.sleep(self._settings.mongo_startup_retry_delay_seconds)
        raise RuntimeError(f"MongoDB is unavailable after startup retries: {last_error}")

    async def ensure_indexes(self) -> None:
        """Create the TTL index on purge_at and an operational module index.

        ``expires_at`` is checked by the application; ``purge_at`` is the field
        MongoDB's TTL monitor uses. Keeping them separate lets expired documents
        survive for stale_cache fallback before automatic cleanup.
        """
        await self._create_ttl_index()
        await self.collection.create_index(
            [("module", ASCENDING), ("cached_at", DESCENDING)],
            name=self.MODULE_INDEX_NAME,
        )

    async def _create_ttl_index(self) -> None:
        try:
            await self.collection.create_index(
                [("purge_at", ASCENDING)],
                name=self.TTL_INDEX_NAME,
                expireAfterSeconds=0,
            )
        except OperationFailure as exc:
            # Index already exists with different options: recreate it.
            if getattr(exc, "code", None) != 85:
                raise
            logger.warning("Recreating TTL index %s with updated options", self.TTL_INDEX_NAME)
            await self.collection.drop_index(self.TTL_INDEX_NAME)
            await self.collection.create_index(
                [("purge_at", ASCENDING)],
                name=self.TTL_INDEX_NAME,
                expireAfterSeconds=0,
            )

    async def ping(self) -> bool:
        """Lightweight readiness probe used by /api/v1/health."""
        try:
            await self.client.admin.command("ping")
            return True
        except PyMongoError as exc:
            logger.warning("MongoDB ping failed: %s", exc)
            return False

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            self._collection = None
