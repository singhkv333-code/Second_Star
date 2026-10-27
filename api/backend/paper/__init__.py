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
from backend.paper.evaluator import evaluate_resting_orders
from backend.paper.fills import cancel_resting_order, fill_resting_order
from backend.paper.jobs import snapshot_all_navs, tick_paper_accounts
from backend.paper.routing import (
    paper_position_qty,
    should_use_paper,
    submit_gtt,
    submit_gtt_for_user,
    submit_order,
    submit_order_for_user,
)
from backend.paper.portfolio import (
    account_summary,
    fills_journal,
    holdings,
    nav_curve,
    open_orders,
)
from backend.paper.positions import (
    paper_open_orders_kite_shape,
    paper_positions_kite_shape,
)
from backend.paper.snapshots import latest_nav, nav_series, snapshot_account_nav
from backend.paper.valuation import compute_account_nav, mark_positions
from backend.paper.ideas import resolve_idea
from backend.paper.idea_valuation import compute_idea_nav, compute_idea_positions
from backend.paper.scorecards import (
    idea_detail,
    idea_nav_series,
    ideas_list,
    latest_idea_nav,
    refresh_all_idea_scorecards,
    refresh_idea_scorecard,
    snapshot_idea_nav,
)

__all__ = [
    "PaperBroker",
    "get_or_create_account",
    "should_use_paper",
    "paper_position_qty",
    "submit_order",
    "submit_gtt",
    "submit_order_for_user",
    "submit_gtt_for_user",
    # P3 — resting fills, valuation, snapshots, scheduler orchestrators
    "fill_resting_order",
    "cancel_resting_order",
    "evaluate_resting_orders",
    "mark_positions",
    "compute_account_nav",
    "snapshot_account_nav",
    "latest_nav",
    "nav_series",
    "tick_paper_accounts",
    "snapshot_all_navs",
    # P4 — REST read service + kite-shaped reads
    "account_summary",
    "holdings",
    "open_orders",
    "fills_journal",
    "nav_curve",
    "paper_positions_kite_shape",
    "paper_open_orders_kite_shape",
    # P6 — forward-test ideas: resolver, idea-grain valuation/snapshots,
    # scorecard refresh + read service
    "resolve_idea",
    "compute_idea_nav",
    "compute_idea_positions",
    "snapshot_idea_nav",
    "latest_idea_nav",
    "idea_nav_series",
    "refresh_idea_scorecard",
    "refresh_all_idea_scorecards",
    "ideas_list",
    "idea_detail",
]
