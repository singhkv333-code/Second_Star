"""watchlist_items table — backs action.update_watchlist in the Agent
System and a future per-user watchlist UI surface.

Plain table (not a JSON array on users) so UNIQUE (user_id, symbol,
exchange) is DB-enforced and future fields drop in without JSON
migrations. See models.py:WatchlistItem.

Revision ID: 0002_watchlist
Revises: 0001_workflows
Create Date: 2026-05-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0002_watchlist"
down_revision: Union[str, None] = "0001_workflows"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "watchlist_items",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id"), nullable=False, index=True,
        ),
        sa.Column("symbol", sa.String(64), nullable=False),
        sa.Column(
            "exchange", sa.String(8), nullable=False, server_default="NSE",
        ),
        sa.Column(
            "added_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "user_id", "symbol", "exchange",
            name="uq_watchlist_items_user_symbol_exchange",
        ),
    )


def downgrade() -> None:
    op.drop_table("watchlist_items")
