"""Auto-discovery and registration of connector plugins."""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
import re
from typing import TYPE_CHECKING

import httpx

from app.connectors.base import BaseConnector

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_SKIP_MODULES = frozenset({"base", "registry"})


class ConnectorLoadError(RuntimeError):
    """Raised when a connector plugin is invalid or cannot be imported."""


class ConnectorRegistry:
    def __init__(self) -> None:
        self._classes: dict[str, type[BaseConnector]] = {}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._classes))

    def register(self, connector_cls: type[BaseConnector]) -> None:
        if not inspect.isclass(connector_cls) or not issubclass(connector_cls, BaseConnector):
            raise ConnectorLoadError(
                f"{connector_cls!r} must be a subclass of BaseConnector"
            )
        if inspect.isabstract(connector_cls):
            raise ConnectorLoadError(
                f"Connector {connector_cls.__name__} is abstract and cannot be registered"
            )

        name = getattr(connector_cls, "name", None)
        if not isinstance(name, str) or not _NAME_PATTERN.fullmatch(name):
            raise ConnectorLoadError(
                f"Connector {connector_cls.__name__} must declare a valid snake_case name"
            )
        if name in self._classes:
            raise ConnectorLoadError(
                f"Duplicate connector name '{name}' from {connector_cls.__module__}"
            )
        self._classes[name] = connector_cls
        logger.debug("Registered connector %s -> %s", name, connector_cls.__name__)

    def create_all(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient,
    ) -> dict[str, BaseConnector]:
        connectors: dict[str, BaseConnector] = {}
        for name, connector_cls in sorted(self._classes.items()):
            connector = connector_cls(settings, http_client)
            if connector.name != name:
                raise ConnectorLoadError(
                    f"Connector instance name mismatch: expected '{name}', got '{connector.name}'"
                )
            connectors[name] = connector
        return connectors


_registry = ConnectorRegistry()
_discovered_modules: tuple[str, ...] = ()


def get_registry() -> ConnectorRegistry:
    return _registry


def register_connector(
    connector_cls: type[BaseConnector],
) -> type[BaseConnector]:
    """Class decorator used inside each connector module."""
    _registry.register(connector_cls)
    return connector_cls


def discover_connectors(package: str = "app.connectors", *, force: bool = False) -> tuple[str, ...]:
    """Import every module in the connector package so decorators execute.

    Returns the imported module names for startup diagnostics. Discovery is
    idempotent; ``force=True`` re-scans the filesystem for newly added files.
    """
    global _discovered_modules
    if _discovered_modules and not force:
        return _discovered_modules

    package_module = importlib.import_module(package)
    package_path = getattr(package_module, "__path__", None)
    if package_path is None:
        raise ConnectorLoadError(f"{package} is not a package")

    imported: list[str] = []
    for module_info in pkgutil.iter_modules(package_path):
        name = module_info.name
        if name.startswith("_") or name in _SKIP_MODULES or module_info.ispkg:
            continue
        qualified_name = f"{package}.{name}"
        try:
            importlib.import_module(qualified_name)
        except Exception as exc:
            message = f"Failed to import connector module {qualified_name}: {exc}"
            raise ConnectorLoadError(message) from exc
        imported.append(qualified_name)

    _discovered_modules = tuple(imported)
    return _discovered_modules
