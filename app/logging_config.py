"""Root logging configuration."""

from __future__ import annotations

import logging

from app.config import Settings


def configure_logging(settings: Settings) -> None:
    level = getattr(logging, settings.log_level, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)

    if root.handlers:
        for handler in root.handlers:
            handler.setLevel(level)
            if not handler.formatter:
                handler.setFormatter(_formatter())
    else:
        handler = logging.StreamHandler()
        handler.setLevel(level)
        handler.setFormatter(_formatter())
        root.addHandler(handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("pymongo").setLevel(logging.WARNING)


def _formatter() -> logging.Formatter:
    return logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
