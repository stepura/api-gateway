"""Simulated external SQL/NoSQL analytics repository with artificial latency."""

from __future__ import annotations

import asyncio
import hashlib
import random
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.connectors.base import BaseConnector
from app.connectors.registry import register_connector
from app.utils import iso_utc, utc_now

_STATUSES = ("paid", "pending", "cancelled", "refunded")
_CURRENCIES = ("USD", "EUR", "RUB")


class SlowRepositoryParams(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tenant_id: str = Field(
        default="acme",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Tenant identifier in the simulated warehouse",
    )
    limit: int = Field(default=5, ge=1, le=50, description="Number of orders to return")

    @field_validator("tenant_id")
    @classmethod
    def normalize_tenant(cls, value: str) -> str:
        return value.strip().lower()


@register_connector
class SlowRepositoryConnector(BaseConnector):
    name = "slow_repository"
    description = (
        "Simulated external analytics warehouse/parser query with a configurable "
        "2-second latency (deterministic dataset)"
    )
    params_model = SlowRepositoryParams

    @property
    def cache_ttl_seconds(self) -> int:
        return self.settings.slow_repository_ttl_seconds

    async def fetch(self, params: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self.prepare_params(params)
        tenant_id = str(normalized["tenant_id"])
        limit = int(normalized["limit"])

        # Intentional I/O latency of an external warehouse/parser. Default is 2s.
        await asyncio.sleep(self.settings.slow_connector_delay_seconds)

        seed_material = f"{tenant_id}:{limit}".encode()
        seed = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
        rng = random.Random(seed)

        orders: list[dict[str, Any]] = []
        for index in range(1, limit + 1):
            orders.append(
                {
                    "order_id": f"ORD-{tenant_id.upper()}-{index:04d}",
                    "amount": round(rng.uniform(15.0, 950.0), 2),
                    "currency": rng.choice(_CURRENCIES),
                    "status": rng.choice(_STATUSES),
                    "items": rng.randint(1, 8),
                }
            )

        total = round(sum(float(order["amount"]) for order in orders), 2)
        return {
            "source_system": "simulated-analytics-warehouse",
            "tenant_id": tenant_id,
            "query": {"limit": limit},
            "orders": orders,
            "summary": {
                "orders_count": len(orders),
                "total_amount": total,
                "average_amount": round(total / len(orders), 2),
                "paid_orders": sum(1 for order in orders if order["status"] == "paid"),
            },
            "fetched_at": iso_utc(utc_now()),
        }
