"""Broker-agnostic connection layer.

One ``BrokerConnector`` per supported broker (Kite, Dhan, Fyers...). The
registry resolves a connector by name; the order-routing seam
(``paper/routing.py``), the token-refresh scheduler, the portfolio reads, and
the ``/brokers`` router all talk to this interface instead of
``backend.kite.*`` directly.

This REPLACES the old Kite-hardcoded connection path — there is exactly one
connection + onboarding system, keyed on ``BrokerSession.broker``.
"""
from backend.brokers.base import (
    BrokerConnector,
    BrokerInfo,
    DeepLinks,
    NeedsManualLogin,
    PersistenceKind,
)

__all__ = [
    "BrokerConnector",
    "BrokerInfo",
    "DeepLinks",
    "NeedsManualLogin",
    "PersistenceKind",
]
