"""Abstract adapter contract shared by all external-source plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, ClassVar, cast

import httpx
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.errors import InvalidParamsError
from app.utils import canonicalize_json, validation_error_details


class BaseConnector(ABC):
    """Base class for a pluggable data source.

    Subclasses declare a unique ``name``, an optional Pydantic ``params_model``
    for validation/normalization, implement ``fetch`` and override
    ``cache_ttl_seconds`` when the global default is not appropriate.
    """

    name: ClassVar[str]
    description: ClassVar[str] = "External data connector"
    params_model: ClassVar[type[BaseModel] | None] = None

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.http_client = http_client

    @property
    def cache_ttl_seconds(self) -> int:
        return self.settings.default_cache_ttl_seconds

    @property
    def http_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            self.settings.http_timeout_seconds,
            connect=self.settings.http_connect_timeout_seconds,
        )

    def prepare_params(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and normalize params before hashing them into a cache key."""
        raw = dict(params)
        if self.params_model is None:
            return cast(dict[str, Any], canonicalize_json(raw))
        try:
            model = self.params_model.model_validate(raw)
        except ValidationError as exc:
            raise InvalidParamsError(
                f"Invalid parameters for module '{self.name}'",
                module=self.name,
                details=validation_error_details(exc),
            ) from exc
        return cast(dict[str, Any], model.model_dump(mode="json", exclude_none=True))

    @abstractmethod
    async def fetch(self, params: Mapping[str, Any]) -> Any:
        """Fetch fresh data from the upstream source.

        Implementations must convert all transport/domain failures into
        ``ConnectorUpstreamError`` so the orchestrator can apply the cache
        fallback policy.
        """

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "parameters_schema": (
                self.params_model.model_json_schema() if self.params_model is not None else None
            ),
        }
