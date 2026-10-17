"""Broker connector registry.

Single source of truth for "which connector handles this broker". The
``/brokers`` router, the order-routing seam, the token-refresh scheduler, and
the portfolio reads resolve a :class:`BrokerConnector` by name through here
instead of importing ``KiteConnector`` / ``DhanConnector`` directly.

Connectors are instantiated once at import and reused (they're stateless —
every method takes the ``BrokerSession`` row it operates on).
"""
from __future__ import annotations

from backend.brokers.angelone import AngelOneConnector
from backend.brokers.base import BrokerConnector
from backend.brokers.dhan import DhanConnector
from backend.brokers.fyers import FyersConnector
from backend.brokers.groww import GrowwConnector
from backend.brokers.kite import KiteConnector
from backend.brokers.upstox import UpstoxConnector

# Instantiate each connector exactly once. Stateless, so a module-level
# singleton per broker is fine.
_CONNECTORS: dict[str, BrokerConnector] = {
    "kite": KiteConnector(),
    "groww": GrowwConnector(),
    "angelone": AngelOneConnector(),
    "upstox": UpstoxConnector(),
    "dhan": DhanConnector(),
    "fyers": FyersConnector(),
}

# Stable display/order for the FE broker picker.
# Ordered by Indian retail market share, then by how little the user has to
# type: Zerodha and Upstox are pure OAuth (nothing to type at all).
SUPPORTED_BROKERS: list[str] = [
    "kite", "groww", "angelone", "upstox", "dhan", "fyers",
]


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
