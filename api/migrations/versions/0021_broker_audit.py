"""broker_audit: append-only broker order/token audit trail.

Adds the ``broker_audit`` table backing the new ``BrokerAudit`` model
(backend/models.py). One row per broker-side event written by
``backend/brokers/audit.py::record_audit``:
  - order routing auto-exec gating — ``order_intent`` (armed/registered),
    ``order_placed`` (live connector placement), ``order_failed``.
  - the daily scheduler token sweep — ``token_refresh`` /
    ``token_refresh_failed``.

Deliberately FK-light: ``user_id`` is a nullable FK to ``users.id`` (an
event may pre-date a resolvable user) and there is no relationship, so an
audit write can never couple to / break the order or session it records.
Indexes on ``event_type`` and ``created_at`` back the two reads we expect
("events of kind X" and "recent events").

Additive-only — no ALTER on existing tables.

Revision ID: 0021_broker_audit
Revises: 0020_broker_sessions
Create Date: 2026-06-20
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0021_broker_audit"
down_revision: Union[str, None] = "0020_broker_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "broker_audit",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("broker", sa.String(20), nullable=True),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("symbol", sa.String(64), nullable=True),
        sa.Column("side", sa.String(8), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("order_type", sa.String(16), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("order_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_broker_audit_user_id", "broker_audit", ["user_id"],
    )
    op.create_index(
        "ix_broker_audit_event_type", "broker_audit", ["event_type"],
    )
    op.create_index(
        "ix_broker_audit_created_at", "broker_audit", ["created_at"],
    )

    logger.info(
        "0021_broker_audit: created broker_audit (dialect=%s).",
        bind.dialect.name,
    )


def downgrade() -> None:
    op.drop_index("ix_broker_audit_created_at", table_name="broker_audit")
    op.drop_index("ix_broker_audit_event_type", table_name="broker_audit")
    op.drop_index("ix_broker_audit_user_id", table_name="broker_audit")
    op.drop_table("broker_audit")
