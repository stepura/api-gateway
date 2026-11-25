"""Unit tests for deterministic cache-key generation."""

from __future__ import annotations

import re

from app.services.cache_service import CacheService


def test_key_is_independent_of_mapping_order() -> None:
    first = {"z": 1, "a": {"y": [3, {"b": 2, "a": 1}], "x": None}}
    second = {"a": {"x": None, "y": [3, {"a": 1, "b": 2}]}, "z": 1}
    assert CacheService.build_key("rates", first) == CacheService.build_key("rates", second)


def test_key_is_scoped_by_module_and_params() -> None:
    params = {"base": "USD"}
    assert CacheService.build_key("rates", params) != CacheService.build_key("weather", params)
    assert CacheService.build_key("rates", params) != CacheService.build_key(
        "rates", {"base": "EUR"}
    )


def test_key_is_sha256_hex() -> None:
    key = CacheService.build_key("rates", {"base": "USD", "symbols": ["EUR", "RUB"]})
    assert re.fullmatch(r"[0-9a-f]{64}", key)
