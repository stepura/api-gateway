"""FastAPI application factory and lifespan."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.exception_handlers import register_exception_handlers
from app.api.v1.router import api_router
from app.config import get_settings
from app.container import AppContainer
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings)
    logger.info(
        "Starting %s (env=%s, version=%s)",
        settings.app_name,
        settings.app_env,
        __version__,
    )

    container = await AppContainer.create(settings)
    app.state.container = container
    logger.info("Loaded connectors: %s", ", ".join(sorted(container.connectors)))

    try:
        yield
    finally:
        logger.info("Shutting down %s", settings.app_name)
        await container.aclose()
        app.state.container = None


def create_app() -> FastAPI:
    application = FastAPI(
        title="API Gateway / Aggregator",
        description=(
            "Modular async REST gateway with per-connector TTL, deterministic "
            "MongoDB caching and stale-cache fallback."
        ),
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    application.include_router(api_router)
    register_exception_handlers(application)
    return application


app = create_app()
