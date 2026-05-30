"""Paper-trading simulated broker (P1).

A PaperBroker fills orders against live prices and accrues them into the
structured portfolio defined in backend/models.py (paper_* tables). It is
the non-Kite execution path: chat + workflow actions route here when an
account is in ``mode='paper'`` (the routing shim lands in P2).

Public surface:
    PaperBroker            — place_order / place_gtt_order, mirrors the
                             kite.orders interface plus client_request_id.
    get_or_create_account  — seed/lookup a user's paper book.

Money discipline: reconciled-cash columns are Numeric -> read back as
decimal.Decimal. Everything in this package does money math in Decimal
(see backend/paper/money.py) and casts to float() only at the JSON edge.
"""
from backend.paper.accounts import get_or_create_account
from backend.paper.broker import PaperBroker

__all__ = ["PaperBroker", "get_or_create_account"]
