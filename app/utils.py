"""Small, dependency-free helpers shared by the cache, connectors and API layer."""

from __future__ import annotations

import base64
import json
import math
from collections.abc import Mapping
from datetime import UTC, date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def to_naive_utc(value: datetime) -> datetime:
    """Convert a datetime to naive UTC, the representation stored by BSON."""
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).replace(tzinfo=None)


def to_aware_utc(value: datetime) -> datetime:
    """Interpret naive BSON datetimes as UTC and normalize aware ones to UTC."""
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC)


def iso_utc(value: datetime | None) -> str | None:
    """Render a datetime as an ISO-8601 string with a trailing Z, or return None."""
    if value is None:
        return None
    return to_aware_utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonicalize_json(value: Any) -> Any:
    """Recursively convert a value into deterministically orderable JSON primitives.

    Object keys are sorted, sets are sorted by their canonical JSON representation,
    and non-JSON standard-library types are converted to stable strings. The result
    is safe to feed into ``json.dumps(sort_keys=True, allow_nan=False)``.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("NaN and Infinity are not allowed in cache keys")
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return canonicalize_json(value.value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, datetime):
        return iso_utc(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, Mapping):
        items = sorted(value.items(), key=lambda item: str(item[0]))
        return {str(key): canonicalize_json(item) for key, item in items}
    if isinstance(value, (list, tuple)):
        return [canonicalize_json(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized = [canonicalize_json(item) for item in value]
        return sorted(normalized, key=canonical_json)
    raise TypeError(f"Unsupported value type for canonical JSON: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Serialize a value to a compact, deterministic JSON string."""
    return json.dumps(
        canonicalize_json(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def validation_error_details(exc: Any) -> list[dict[str, Any]]:
    """Extract validation errors without URLs, across Pydantic 2.x versions."""
    return [
        {key: value for key, value in error.items() if key != "url"}
        for error in exc.errors()
    ]
