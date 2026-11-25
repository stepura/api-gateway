"""Public request/response contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ResponseSource(StrEnum):
    LIVE = "live"
    CACHE = "cache"
    STALE_CACHE = "stale_cache"
    # Technical value used for errors and non-cacheable system responses when
    # no data source produced a payload.
    NONE = "none"


class QueryEnvelope(BaseModel):
    """The single response shape used by every endpoint."""

    model_config = ConfigDict(extra="forbid")

    module: str = Field(description="Connector or internal module name")
    source: ResponseSource = Field(description="Where the returned data came from")
    cached_at: str | None = Field(
        default=None,
        description="ISO-8601 UTC time when the returned snapshot was written to cache",
    )
    data: Any = Field(description="Module payload or structured error payload")


class QueryRequest(BaseModel):
    """POST body: arbitrary connector parameters."""

    model_config = ConfigDict(extra="forbid")

    params: dict[str, Any] = Field(default_factory=dict)


class ErrorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: Any | None = None
