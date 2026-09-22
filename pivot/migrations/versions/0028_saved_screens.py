"""saved_screens: the user's kept screens, from chat or from the Screener.

Backs backend.models.SavedScreen. One table for both kinds the user calls "my
screens":

  kind="symbols"  a frozen membership (``symbols``), which is what a screen
                  handed over from the chart's chat becomes — charto screens
                  its own 500-instrument daily matrix in a feature vocabulary
                  this service does not share, so the filters cannot be
                  replayed here; we keep what it matched plus ``criteria`` and
                  ``as_of`` so the screen can always say what it was and when.
  kind="filters"  this service's own query (``filters``), re-run on open.

``symbols``/``filters`` are JSONB on Postgres and JSON on SQLite, matching the
model's with_variant so create_all (the test DB) and this migration agree on
the physical type. ``kind`` is a VARCHAR + CHECK rather than a native ENUM, for
the same reason the rest of this schema avoids native enums.

Additive-only — creates one table, no ALTER on anything existing.

BRANCHES OFF 0026, NOT 0027, deliberately. Production sits at 0026 and
0027_drop_view_markets (irreversible: 7 tables dropped CASCADE + 6 enum types,
89 rows still live at the time of writing) had not been applied. Chaining this
migration onto it would have made adding a screens table drop that data as a
side effect. So 0026 has two children until someone applies 0027 on purpose and
adds a merge revision; until then `alembic upgrade head` reports multiple heads
and this one is applied by name:

    alembic upgrade 0028_saved_screens

Revision ID: 0028_saved_screens
Revises: 0026_option_expiry_settlement
Create Date: 2026-09-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0028_saved_screens"
down_revision: Union[str, None] = "0026_option_expiry_settlement"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "saved_screens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "kind", sa.String(length=16), nullable=False,
            server_default="symbols",
        ),
        sa.Column(
            "source", sa.String(length=16), nullable=False,
            server_default="screener",
        ),
        sa.Column(
            "symbols",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(
                sa.JSON(), "sqlite",
            ),
            nullable=True,
        ),
        sa.Column(
            "filters",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(
                sa.JSON(), "sqlite",
            ),
            nullable=True,
        ),
        sa.Column("criteria", sa.Text(), nullable=True),
        sa.Column("as_of", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('symbols', 'filters')", name="ck_saved_screens_kind",
        ),
        sa.UniqueConstraint(
            "user_id", "name", name="uq_saved_screens_user_name",
        ),
    )
    op.create_index(
        "ix_saved_screens_user_id", "saved_screens", ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_saved_screens_user_id", table_name="saved_screens")
    op.drop_table("saved_screens")
