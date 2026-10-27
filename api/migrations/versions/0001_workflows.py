"""Pivot Agent System v1 — initial workflow tables.

Mirrors docs/ARCHITECTURE.md §4. Six new tables, three Postgres enums.
Sync SQLAlchemy 2.0 + psycopg2; uses postgresql.JSONB for runtime data
bags and postgresql.UUID for primary keys (server-side gen_random_uuid()
for compatibility with the Postgres deploy target).

Webhook tokens live in their own table (workflow_webhook_tokens) so
secrets never appear in workflow_steps.config JSON — see ARCHITECTURE.md
§7 invariant 7.

Revision ID: 0001_workflows
Revises:
Create Date: 2026-05-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_workflows"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Postgres enum types declared once and re-used.
WORKFLOW_STATUS = postgresql.ENUM(
    "draft", "active", "paused", "archived",
    name="workflow_status",
    create_type=False,
)
RUN_STATUS = postgresql.ENUM(
    "running", "succeeded", "failed", "cancelled", "awaiting_approval",
    name="run_status",
    create_type=False,
)
STEP_STATUS = postgresql.ENUM(
    "pending", "running", "succeeded", "failed", "skipped", "awaiting_approval",
    name="step_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()

    # Enable pgcrypto so gen_random_uuid() is available for UUID defaults.
    # Safe / idempotent on Postgres; skipped on other dialects.
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

        op.execute(
            "CREATE TYPE workflow_status AS ENUM "
            "('draft', 'active', 'paused', 'archived');"
        )
        op.execute(
            "CREATE TYPE run_status AS ENUM "
            "('running', 'succeeded', 'failed', 'cancelled', 'awaiting_approval');"
        )
        op.execute(
            "CREATE TYPE step_status AS ENUM "
            "('pending', 'running', 'succeeded', 'failed', "
            "'skipped', 'awaiting_approval');"
        )

    # ── workflows ───────────────────────────────────────────────────
    op.create_table(
        "workflows",
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
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            WORKFLOW_STATUS,
            nullable=False,
            server_default=sa.text("'draft'::workflow_status"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "single_instance",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.create_index("ix_workflows_user_id", "workflows", ["user_id"])

    # ── workflow_steps ──────────────────────────────────────────────
    op.create_table(
        "workflow_steps",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workflow_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step_type", sa.Text(), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("label", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "workflow_id", "step_index",
            name="uq_workflow_step_index",
        ),
    )
    op.create_index(
        "ix_workflow_steps_workflow_id",
        "workflow_steps",
        ["workflow_id"],
    )

    # ── workflow_runs ───────────────────────────────────────────────
    op.create_table(
        "workflow_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workflow_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("workflows.id"),
            nullable=False,
        ),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("triggered_by", sa.Text(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            RUN_STATUS,
            nullable=False,
            server_default=sa.text("'running'::run_status"),
        ),
        sa.Column("halt_reason", sa.Text(), nullable=True),
        sa.Column(
            "context",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        # Defense-in-depth: keep direct SQL inserts honest. Pydantic
        # Literal validates at the API boundary; this guards the DB.
        sa.CheckConstraint(
            "triggered_by IN ('schedule', 'manual', 'webhook', "
            "'price_alert', 'indicator_alert', 'event_alert')",
            name="ck_workflow_runs_triggered_by",
        ),
    )
    # Index per ARCHITECTURE.md §4: list-by-workflow + newest-first.
    op.create_index(
        "ix_workflow_runs_workflow_started",
        "workflow_runs",
        ["workflow_id", sa.text("started_at DESC")],
    )

    # ── workflow_run_steps ──────────────────────────────────────────
    op.create_table(
        "workflow_run_steps",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step_type", sa.Text(), nullable=False),
        sa.Column("status", STEP_STATUS, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "output",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.create_index(
        "ix_workflow_run_steps_run_index",
        "workflow_run_steps",
        ["run_id", "step_index"],
    )

    # ── workflow_approvals ──────────────────────────────────────────
    op.create_table(
        "workflow_approvals",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("workflow_runs.id"),
            nullable=False,
        ),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_workflow_approvals_run_id",
        "workflow_approvals",
        ["run_id"],
    )

    # ── workflow_webhook_tokens ─────────────────────────────────────
    # Token IS the primary key. Stored separately so it never appears
    # in workflow_steps.config JSON.
    op.create_table(
        "workflow_webhook_tokens",
        sa.Column("token", sa.Text(), primary_key=True),
        sa.Column(
            "workflow_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_workflow_webhook_tokens_workflow_id",
        "workflow_webhook_tokens",
        ["workflow_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index(
        "ix_workflow_webhook_tokens_workflow_id",
        table_name="workflow_webhook_tokens",
    )
    op.drop_table("workflow_webhook_tokens")

    op.drop_index(
        "ix_workflow_approvals_run_id",
        table_name="workflow_approvals",
    )
    op.drop_table("workflow_approvals")

    op.drop_index(
        "ix_workflow_run_steps_run_index",
        table_name="workflow_run_steps",
    )
    op.drop_table("workflow_run_steps")

    op.drop_index(
        "ix_workflow_runs_workflow_started",
        table_name="workflow_runs",
    )
    op.drop_table("workflow_runs")

    op.drop_index(
        "ix_workflow_steps_workflow_id",
        table_name="workflow_steps",
    )
    op.drop_table("workflow_steps")

    op.drop_index("ix_workflows_user_id", table_name="workflows")
    op.drop_table("workflows")

    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS step_status;")
        op.execute("DROP TYPE IF EXISTS run_status;")
        op.execute("DROP TYPE IF EXISTS workflow_status;")
