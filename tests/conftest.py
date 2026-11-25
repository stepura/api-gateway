"""Test app with an in-memory MongoDB and a mocked external HTTP transport."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

import app.database as database_module
from app.config import get_settings
from app.main import app

UPSTREAM_RATES: dict[str, Any] = {
    "result": "success",
    "provider": "https://www.exchangerate-api.com",
    "base_code": "USD",
    "time_last_update_utc": "Wed, 25 Nov 2026 00:02:31 +0000",
    "time_next_update_utc": "Thu, 26 Nov 2026 00:31:41 +0000",
    "rates": {"USD": 1.0, "EUR": 0.91245, "RUB": 81.96636, "GBP": 0.763269},
}


def _mock_upstream_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if not path.startswith("/v6/latest/"):
        return httpx.Response(404, json={"error": "unexpected path"})
    base = path.rsplit("/", 1)[-1]
    if base == "XXX":
        return httpx.Response(503, json={"result": "error"})
    payload = {**UPSTREAM_RATES, "base_code": base}
    return httpx.Response(200, json=payload)


def _mongo_client_factory(*_args: Any, **_kwargs: Any) -> AsyncMongoMockClient:
    return AsyncMongoMockClient()


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    env = {
        "APP_ENV": "test",
        "LOG_LEVEL": "WARNING",
        "MONGODB_URI": "mongodb://localhost:27017/test_gateway?authSource=admin",
        "MONGODB_DATABASE": "test_gateway",
        "MONGODB_CACHE_COLLECTION": "response_cache",
        "MONGO_STARTUP_ATTEMPTS": "1",
        "MONGO_STARTUP_RETRY_DELAY_SECONDS": "0.1",
        "DEFAULT_CACHE_TTL_SECONDS": "30",
        "STALE_CACHE_RETENTION_SECONDS": "86400",
        "STALE_CACHE_FALLBACK_ENABLED": "true",
        "EXCHANGE_RATES_TTL_SECONDS": "1",
        "SLOW_REPOSITORY_TTL_SECONDS": "30",
        "EXCHANGE_RATES_BASE_URL": "https://open.er-api.com/v6",
        "HTTP_TIMEOUT_SECONDS": "1",
        "SLOW_CONNECTOR_DELAY_SECONDS": "0.02",
        "ENABLE_FAILURE_SIMULATION": "true",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()

    real_async_client = httpx.AsyncClient

    def async_client_factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(_mock_upstream_handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", async_client_factory)
    monkeypatch.setattr(database_module, "AsyncIOMotorClient", _mongo_client_factory)

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()
