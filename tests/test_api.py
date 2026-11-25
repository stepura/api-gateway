"""End-to-end API tests: live -> cache -> force refresh -> stale fallback."""

from __future__ import annotations

import time
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

SLOW_URL = "/api/v1/query/slow_repository?tenant_id=acme&limit=3"
RATES_URL = "/api/v1/query/exchange_rates?base=USD&symbols=EUR,RUB"


def _body(response: Response) -> dict[str, Any]:
    assert response.headers["content-type"].startswith("application/json")
    return response.json()


def test_health_and_module_registry(client: TestClient) -> None:
    health_response = client.get("/api/v1/health")
    assert health_response.status_code == 200
    health = _body(health_response)
    assert health["module"] == "system"
    assert health["source"] == "live"
    assert health["cached_at"] is None
    assert health["data"]["mongo"] == "up"

    registry = _body(client.get("/api/v1/modules"))
    assert registry["source"] == "live"
    modules = {item["name"]: item for item in registry["data"]["modules"]}
    assert {"exchange_rates", "slow_repository"} <= set(modules)
    assert modules["exchange_rates"]["cache_ttl_seconds"] == 1
    assert modules["slow_repository"]["cache_ttl_seconds"] == 30
    assert modules["slow_repository"]["parameters_schema"] is not None


def test_slow_repository_live_cache_and_force_refresh(client: TestClient) -> None:
    first = _body(client.get(SLOW_URL))
    assert first["module"] == "slow_repository"
    assert first["source"] == "live"
    assert first["cached_at"] is not None
    assert first["data"]["summary"]["orders_count"] == 3

    cached = _body(client.get(SLOW_URL))
    assert cached["source"] == "cache"
    assert cached["cached_at"] == first["cached_at"]
    assert cached["data"] == first["data"]

    refreshed = _body(
        client.post(
            "/api/v1/query/slow_repository?force_refresh=true",
            json={"params": {"tenant_id": "acme", "limit": 3}},
        )
    )
    assert refreshed["source"] == "live"
    assert refreshed["cached_at"] is not None
    assert refreshed["data"]["orders"] == first["data"]["orders"]


def test_exchange_rates_live_cache_and_expired_fallback(client: TestClient) -> None:
    first = _body(client.get(RATES_URL))
    assert first["source"] == "live"
    assert first["data"]["base"] == "USD"
    assert set(first["data"]["rates"]) == {"EUR", "RUB"}
    assert first["data"]["requested_symbols"] == ["EUR", "RUB"]

    cached = _body(client.get(RATES_URL))
    assert cached["source"] == "cache"
    assert cached["data"] == first["data"]

    # EXCHANGE_RATES_TTL_SECONDS=1 in the test fixture; the document is kept by
    # the purge_at TTL index and is therefore available as stale_cache.
    time.sleep(1.15)
    stale = _body(client.get(f"{RATES_URL}&simulate_outage=true"))
    assert stale["source"] == "stale_cache"
    assert stale["cached_at"] == first["cached_at"]
    assert stale["data"] == first["data"]


def test_force_refresh_failure_falls_back_to_existing_cache(client: TestClient) -> None:
    first = _body(client.get(SLOW_URL))
    assert first["source"] == "live"

    fallback = _body(client.get(f"{SLOW_URL}&force_refresh=true&simulate_outage=true"))
    assert fallback["source"] == "stale_cache"
    assert fallback["data"] == first["data"]


def test_upstream_failure_without_cache_returns_standard_error(client: TestClient) -> None:
    response = client.get("/api/v1/query/exchange_rates?base=XXX")
    assert response.status_code == 502
    body = _body(response)
    assert body["module"] == "exchange_rates"
    assert body["source"] == "none"
    assert body["cached_at"] is None
    assert body["data"]["code"] == "upstream_unavailable"


def test_unknown_module_and_invalid_connector_params(client: TestClient) -> None:
    unknown = client.get("/api/v1/query/does_not_exist")
    assert unknown.status_code == 404
    body = _body(unknown)
    assert body["data"]["code"] == "unknown_module"
    assert "exchange_rates" in body["data"]["details"]["available_modules"]

    invalid = client.get("/api/v1/query/slow_repository?limit=999")
    assert invalid.status_code == 422
    assert _body(invalid)["data"]["code"] == "invalid_params"

    bad_flag = client.get("/api/v1/query/slow_repository?force_refresh=maybe")
    assert bad_flag.status_code == 422
    assert _body(bad_flag)["data"]["code"] == "validation_error"


def test_cache_invalidation_restores_live_source(client: TestClient) -> None:
    assert _body(client.get(SLOW_URL))["source"] == "live"
    deleted = _body(
        client.request(
            "DELETE",
            "/api/v1/cache/slow_repository",
            json={"params": {"tenant_id": "acme", "limit": 3}},
        )
    )
    assert deleted["module"] == "cache"
    assert deleted["data"]["deleted"] == 1
    assert _body(client.get(SLOW_URL))["source"] == "live"
