"""Broker connector registry.

Single source of truth for "which connector handles this broker". The
``/brokers`` router, the order-routing seam, the token-refresh scheduler, and
the portfolio reads resolve a :class:`BrokerConnector` by name through here
instead of importing ``KiteConnector`` / ``DhanConnector`` directly.

Connectors are instantiated once at import and reused (they're stateless —
every method takes the ``BrokerSession`` row it operates on).
"""
from __future__ import annotations

from backend.brokers.base import BrokerConnector
from backend.brokers.dhan import DhanConnector
from backend.brokers.fyers import FyersConnector
from backend.brokers.kite import KiteConnector

# Instantiate each connector exactly once. Stateless, so a module-level
# singleton per broker is fine.
_CONNECTORS: dict[str, BrokerConnector] = {
    "kite": KiteConnector(),
    "dhan": DhanConnector(),
    "fyers": FyersConnector(),
}

# Stable display/order for the FE broker picker.
SUPPORTED_BROKERS: list[str] = ["kite", "dhan", "fyers"]


def get_connector(broker: str) -> BrokerConnector:
    """Resolve the connector for ``broker``. Raises ``ValueError`` on unknown."""
    try:
        return _CONNECTORS[broker]
    except KeyError:
        raise ValueError(f"Unknown broker: {broker!r}")


def list_connectors() -> list[BrokerConnector]:
    """All connectors in ``SUPPORTED_BROKERS`` order."""
    return [_CONNECTORS[b] for b in SUPPORTED_BROKERS]


def is_supported(broker: str) -> bool:
    """True when ``broker`` has a registered connector."""
    return broker in _CONNECTORS
