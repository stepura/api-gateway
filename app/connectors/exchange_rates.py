"""Connector for the public open.er-api.com exchange-rate REST API."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.connectors.base import BaseConnector
from app.connectors.registry import register_connector
from app.errors import ConnectorUpstreamError

_CODE_PATTERN = re.compile(r"^[A-Z]{3}$")


class ExchangeRatesParams(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    base: str = Field(
        default="USD",
        min_length=3,
        max_length=3,
        description="ISO-4217 base currency code, e.g. USD",
    )
    symbols: list[str] | None = Field(
        default=None,
        description="Optional currency codes as a list or comma-separated string",
    )

    @field_validator("base")
    @classmethod
    def normalize_base(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not _CODE_PATTERN.fullmatch(normalized):
            raise ValueError("base must be a 3-letter currency code")
        return normalized

    @field_validator("symbols", mode="before")
    @classmethod
    def parse_symbols(cls, value: Any) -> list[str] | None:
        if value is None:
            return None
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",") if part.strip()]
        elif isinstance(value, (list, tuple, set, frozenset)):
            parts = [str(part).strip() for part in value if str(part).strip()]
        else:
            raise ValueError("symbols must be a list or a comma-separated string")
        return parts or None

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = sorted({code.strip().upper() for code in value})
        for code in normalized:
            if not _CODE_PATTERN.fullmatch(code):
                raise ValueError(f"invalid currency code: {code}")
        return normalized or None


class ExchangeRatePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    result: str
    provider: str | None = None
    base_code: str
    time_last_update_utc: str
    time_next_update_utc: str | None = None
    rates: dict[str, float]


@register_connector
class ExchangeRatesConnector(BaseConnector):
    name = "exchange_rates"
    description = "Latest fiat exchange rates from the public open.er-api.com API"
    params_model = ExchangeRatesParams

    @property
    def cache_ttl_seconds(self) -> int:
        return self.settings.exchange_rates_ttl_seconds

    async def fetch(self, params: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self.prepare_params(params)
        base = str(normalized["base"])
        symbols = list(normalized.get("symbols") or [])
        url = f"{self.settings.exchange_rates_base_url}/latest/{base}"

        try:
            response = await self.http_client.get(url, timeout=self.http_timeout)
            response.raise_for_status()
            payload = ExchangeRatePayload.model_validate(response.json())
        except httpx.TimeoutException as exc:
            raise ConnectorUpstreamError(
                self.name,
                f"Exchange-rate API timed out: {exc}",
                details={"url": url},
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ConnectorUpstreamError(
                self.name,
                f"Exchange-rate API returned HTTP {exc.response.status_code}",
                details={"url": url, "body": exc.response.text[:200]},
            ) from exc
        except httpx.HTTPError as exc:
            raise ConnectorUpstreamError(
                self.name,
                f"Exchange-rate API transport error: {exc}",
                details={"url": url},
            ) from exc
        except (ValueError, ValidationError) as exc:
            raise ConnectorUpstreamError(
                self.name,
                "Exchange-rate API returned an invalid payload",
                details={"url": url, "reason": str(exc)},
            ) from exc

        if payload.result.lower() != "success":
            raise ConnectorUpstreamError(
                self.name,
                f"Exchange-rate API reported failure: {payload.result}",
                details={"url": url},
            )
        if payload.base_code.upper() != base:
            raise ConnectorUpstreamError(
                self.name,
                f"Exchange-rate API returned base {payload.base_code!r} instead of {base!r}",
                details={"url": url},
            )

        if symbols:
            missing = [code for code in symbols if code not in payload.rates]
            if missing:
                raise ConnectorUpstreamError(
                    self.name,
                    "Requested currency codes are unavailable upstream",
                    details={"missing": missing, "url": url},
                )
            rates = {code: payload.rates[code] for code in symbols}
        else:
            rates = dict(payload.rates)

        return {
            "base": base,
            "rates": rates,
            "rates_count": len(rates),
            "requested_symbols": symbols or None,
            "last_updated_at": payload.time_last_update_utc,
            "next_update_at": payload.time_next_update_utc,
            "provider": payload.provider or "open.er-api.com",
        }
