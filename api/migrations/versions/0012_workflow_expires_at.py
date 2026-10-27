"""Workflow expiry — `workflows.expires_at` (nullable timestamp).

Why (R4b): user prompts like "run this strategy for the next 30 days"
or "until Friday" have no place to land today. The chat layer
correctly told the user the field didn't exist, leaving the constraint
unmodelled. Adds a single nullable timestamp; engine consults it before
firing and auto-deactivates when past.

Single additive ALTER on `workflows`. Existing rows get NULL → "no
expiry", preserving the prior behaviour.

Revision ID: 0012_workflow_expires_at
Revises: 0011_dsl_backtest_runs
Create Date: 2026-05-28
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0012_workflow_expires_at"
down_revision: Union[str, None] = "0011_dsl_backtest_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("workflows", "expires_at")
