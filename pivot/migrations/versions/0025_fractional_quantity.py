"""Fractional paper-book quantities for multi-asset (US fractional shares +
crypto units). Integer → Numeric(18,8) on the paper order/fill/position
quantity columns. Lossless for existing integer rows.

Indian equities/F&O keep integer semantics in code (sizing quantizes to whole
shares / lots); only US equities/ETFs and crypto use the fractional precision.

Revision ID: 0025_fractional_quantity
Revises: 0024_view_positions
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0025_fractional_quantity"
down_revision: Union[str, None] = "0024_view_positions"
branch_labels = None
depends_on = None

_NUM = sa.Numeric(18, 8)
# (table, column, nullable) — the paper-book quantity columns.
_COLS = [
    ("paper_orders", "quantity", False),
    ("paper_orders", "filled_quantity", True),
    ("paper_fills", "quantity", False),
    ("paper_positions", "quantity", False),
]


def upgrade() -> None:
    for table, col, nullable in _COLS:
        op.alter_column(
            table, col,
            existing_type=sa.Integer(),
            type_=_NUM,
            existing_nullable=nullable,
            postgresql_using=f"{col}::numeric(18,8)",
        )


def downgrade() -> None:
    # Round back to integer (fractional US/crypto rows would lose precision —
    # acceptable on a downgrade; the schema is integer-only again).
    for table, col, nullable in _COLS:
        op.alter_column(
            table, col,
            existing_type=_NUM,
            type_=sa.Integer(),
            existing_nullable=nullable,
            postgresql_using=f"round({col})::integer",
        )
