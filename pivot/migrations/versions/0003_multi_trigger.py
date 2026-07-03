"""Multi-trigger workflows.

Adds two columns:

  - workflow_runs.triggered_step_index (int, nullable)
      The step_index of the trigger.* that fired this run. Default
      NULL on legacy rows means "step 0", which is the only valid
      value pre-multi-trigger. The engine reads this to decide which
      branch to execute when the workflow has multiple triggers.

  - workflow_steps.next_run_at (timestamptz, nullable)
      Per-step next-fire time for trigger.schedule. Replaces the
      single workflow.next_run_at as the source of truth for the
      scheduler poll. Indexed on (status of parent workflow, this
      column) for the poll scan, but we add the index on
      next_run_at alone for simplicity.

workflow.next_run_at is kept (not dropped) for backward compatibility
and as a "next fire across all triggers" convenience for the workflows
list endpoint.

Revision ID: 0003_multi_trigger
Revises: 0002_watchlist
Create Date: 2026-05-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0003_multi_trigger"
down_revision: Union[str, None] = "0002_watchlist"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workflow_runs",
        sa.Column(
            "triggered_step_index",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "workflow_steps",
        sa.Column(
            "next_run_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_workflow_steps_next_run_at",
        "workflow_steps",
        ["next_run_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_steps_next_run_at", table_name="workflow_steps")
    op.drop_column("workflow_steps", "next_run_at")
    op.drop_column("workflow_runs", "triggered_step_index")
