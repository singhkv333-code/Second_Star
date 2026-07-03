"""Broker-agnostic sessions: kite_sessions -> broker_sessions.

Replaces the Kite-only ``kite_sessions`` table with the generalized
``broker_sessions`` table (one row per (user, broker)) backing the new
``BrokerSession`` model. The old ``KiteSession`` model is gone; this is a
rename-and-widen, NOT a drop/recreate — existing Kite rows are preserved and
backfilled (``broker='kite'``, ``persistence_mode='daily_oauth'``,
``auto_login_opt_in=false``) via server defaults on the new NOT NULL columns.

Shape changes (mirrors backend/models.py::BrokerSession):
  - rename table     kite_sessions          -> broker_sessions
  - rename column    kite_user_id           -> broker_user_id (keep String(50))
  - + broker            String(20)  NOT NULL default 'kite'
  - + refresh_token     String(500) NULL   (long-lived machine creds, encrypted)
  - + api_key           String(500) NULL
  - + api_secret        String(500) NULL
  - + persistence_mode  String(20)  NOT NULL default 'daily_oauth'
  - + auto_login_opt_in Boolean     NOT NULL default false
  - access_token        -> nullable (token may be absent until first OAuth/mint)
  - totp_secret         -> String(200) (was String(50); fits Fernet ciphertext)
  - DROP old unique(user_id)   (defensively — name varies; was 'unique=True' on
    the legacy column, typically 'kite_sessions_user_id_key' on Postgres)
  - + index on user_id         (model now declares index=True)
  - + unique(user_id, broker)  'uq_broker_sessions_user_broker'

Target is Postgres; plain ops (no batch_alter_table). The legacy unique drop is
guarded so the migration is safe whether the constraint exists or not.

Revision ID: 0020_broker_sessions
Revises: 0019_option_paper_legs
Create Date: 2026-06-20
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0020_broker_sessions"
down_revision: Union[str, None] = "0019_option_paper_legs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")


def _drop_user_id_unique() -> None:
    """Drop the legacy unique constraint/index on broker_sessions.user_id.

    The old ``kite_sessions.user_id`` column carried ``unique=True``, which
    Postgres realises as a unique constraint (usually named
    ``kite_sessions_user_id_key``) — and the table now answers to
    ``broker_sessions`` after the rename above. The name is environment
    dependent (SQLite emits an index instead), so introspect and drop whatever
    is there rather than hard-coding a name.
    """
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # Postgres: unique CONSTRAINT. Drop by discovered name.
    for uc in insp.get_unique_constraints("broker_sessions"):
        if uc.get("column_names") == ["user_id"] and uc.get("name"):
            op.drop_constraint(uc["name"], "broker_sessions", type_="unique")

    # SQLite / others: the same thing may surface as a unique INDEX.
    for ix in insp.get_indexes("broker_sessions"):
        if (
            ix.get("unique")
            and ix.get("column_names") == ["user_id"]
            and ix.get("name")
        ):
            op.drop_index(ix["name"], table_name="broker_sessions")


def upgrade() -> None:
    bind = op.get_bind()

    # 1) Rename the table itself.
    op.rename_table("kite_sessions", "broker_sessions")

    # 2) Drop the legacy unique(user_id) before re-adding user_id as a plain
    #    index — order matters so the new unique(user_id, broker) is the only
    #    uniqueness rule left.
    _drop_user_id_unique()

    # 3) Rename the broker-identity column (type unchanged: String(50)).
    op.alter_column(
        "broker_sessions",
        "kite_user_id",
        new_column_name="broker_user_id",
        existing_type=sa.String(50),
        existing_nullable=True,
    )

    # 4) New columns. NOT NULL ones carry a server_default so the pre-existing
    #    Kite rows backfill in place.
    op.add_column(
        "broker_sessions",
        sa.Column(
            "broker", sa.String(20), nullable=False,
            server_default="kite",
        ),
    )
    op.add_column(
        "broker_sessions",
        sa.Column("refresh_token", sa.String(500), nullable=True),
    )
    op.add_column(
        "broker_sessions",
        sa.Column("api_key", sa.String(500), nullable=True),
    )
    op.add_column(
        "broker_sessions",
        sa.Column("api_secret", sa.String(500), nullable=True),
    )
    op.add_column(
        "broker_sessions",
        sa.Column(
            "persistence_mode", sa.String(20), nullable=False,
            server_default="daily_oauth",
        ),
    )
    op.add_column(
        "broker_sessions",
        sa.Column(
            "auto_login_opt_in", sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ),
    )

    # 5) Widen / relax existing columns to match the model.
    op.alter_column(
        "broker_sessions",
        "access_token",
        existing_type=sa.String(500),
        nullable=True,
    )
    op.alter_column(
        "broker_sessions",
        "totp_secret",
        existing_type=sa.String(50),
        type_=sa.String(200),
        existing_nullable=True,
    )

    # 6) New indexes / constraints. Model declares index=True on user_id and a
    #    composite unique on (user_id, broker).
    op.create_index(
        "ix_broker_sessions_user_id",
        "broker_sessions", ["user_id"],
    )
    op.create_unique_constraint(
        "uq_broker_sessions_user_broker",
        "broker_sessions", ["user_id", "broker"],
    )

    logger.info(
        "0020_broker_sessions: migrated kite_sessions -> broker_sessions "
        "(dialect=%s).",
        bind.dialect.name,
    )


def downgrade() -> None:
    # Reverse of upgrade(). Restore the Kite-only shape.
    op.drop_constraint(
        "uq_broker_sessions_user_broker",
        "broker_sessions", type_="unique",
    )
    op.drop_index(
        "ix_broker_sessions_user_id", table_name="broker_sessions",
    )

    # Restore the narrower totp_secret + NOT NULL access_token.
    op.alter_column(
        "broker_sessions",
        "totp_secret",
        existing_type=sa.String(200),
        type_=sa.String(50),
        existing_nullable=True,
    )
    op.alter_column(
        "broker_sessions",
        "access_token",
        existing_type=sa.String(500),
        nullable=False,
    )

    # Drop the columns added in upgrade().
    op.drop_column("broker_sessions", "auto_login_opt_in")
    op.drop_column("broker_sessions", "persistence_mode")
    op.drop_column("broker_sessions", "api_secret")
    op.drop_column("broker_sessions", "api_key")
    op.drop_column("broker_sessions", "refresh_token")
    op.drop_column("broker_sessions", "broker")

    # Rename the identity column back.
    op.alter_column(
        "broker_sessions",
        "broker_user_id",
        new_column_name="kite_user_id",
        existing_type=sa.String(50),
        existing_nullable=True,
    )

    # Restore the legacy unique(user_id), then rename the table back.
    op.create_unique_constraint(
        "kite_sessions_user_id_key",
        "broker_sessions", ["user_id"],
    )
    op.rename_table("broker_sessions", "kite_sessions")
