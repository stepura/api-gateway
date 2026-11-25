"""Connector plugin package.

New sources are added by creating a module in this package and decorating its
BaseConnector subclass with ``@register_connector``. No core file changes.
"""

from app.connectors.base import BaseConnector
from app.connectors.registry import (
    ConnectorLoadError,
    ConnectorRegistry,
    discover_connectors,
    get_registry,
    register_connector,
)

__all__ = [
    "BaseConnector",
    "ConnectorLoadError",
    "ConnectorRegistry",
    "discover_connectors",
    "get_registry",
    "register_connector",
]
