"""view_positions: the "My Views" per-user deployment ledger.

Backs backend.models.ViewPosition — one row per user deployment of a view
expression (register-not-execute: the ledger records what the user armed;
Pivot never places or exits orders). ``legs``/``exits`` are JSONB snapshots;
``status`` is a plain VARCHAR + CHECK (the model declares
SQLEnum(native_enum=False), so SQLite's create_all renders the same CHECK).

Within the View Markets domain PK/FKs are uuids (hard FKs, CASCADE);
``workflow_id`` is a SOFT cross-domain reference (no FK) — mirrors
view_expressions.workflow_id in 0023.

Additive-only — no ALTER on existing tables.

Revision ID: 0024_view_positions
Revises: 0023_view_markets
Create Date: 2026-07-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0024_view_positions"
down_revision: Union[str, None] = "0023_view_markets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "view_positions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "view_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("market_views.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "expression_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("view_expressions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # SOFT cross-domain ref to the armed workflow draft (no FK).
        sa.Column("workflow_id", sa.String(36), nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        sa.Column("capital_inr", sa.Float(), nullable=True),
        sa.Column(
            "open_fraction",
            sa.Float(),
            nullable=False,
            server_default=sa.text("1.0"),
        ),
        sa.Column("take_profit_pct", sa.Float(), nullable=True),
        sa.Column("stop_loss_pct", sa.Float(), nullable=True),
        sa.Column(
            "legs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "exits",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("realized_pnl_inr", sa.Float(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "entry_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("exited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'exited')",
            name="ck_view_positions_status",
        ),
    )
    op.create_index("ix_view_positions_user_id", "view_positions", ["user_id"])
    op.create_index("ix_view_positions_view_id", "view_positions", ["view_id"])
    op.create_index(
        "ix_view_positions_expression_id", "view_positions", ["expression_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_view_positions_expression_id", table_name="view_positions"
    )
    op.drop_index("ix_view_positions_view_id", table_name="view_positions")
    op.drop_index("ix_view_positions_user_id", table_name="view_positions")
    op.drop_table("view_positions")
