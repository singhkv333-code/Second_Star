"""view_markets: belief -> expression -> deployment (View Markets V2, Phase 1).

Backs the new models in backend/models.py (MarketView, ViewExpression,
ViewTransmission, ViewConfidence, ViewExpectation, ViewFollow). Spec:
Markdowns/Version2.md; scope contract: Markdowns/VIEW_MARKETS_PLAN.md.

Six new tables, six Postgres enum types. Sync SQLAlchemy 2.0 + psycopg2; uses
postgresql.UUID PKs with server-side gen_random_uuid() (mirrors 0001_workflows)
and postgresql.JSONB for the expression config bag. Enum columns are real
Postgres ENUM types here; the models declare them as SQLEnum(native_enum=False)
so the SQLite test DB (Base.metadata.create_all) renders a CHECK instead.

Within the View Markets domain every PK/FK is a uuid, so the child tables hold
HARD FKs to market_views(id) with ON DELETE CASCADE. view_expressions'
backtest_run_id / workflow_id are SOFT references (no FK) to other domains.

Additive-only — no ALTER on existing tables.

Revision ID: 0023_view_markets
Revises: 0022_user_auth_beta
Create Date: 2026-06-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0023_view_markets"
down_revision: Union[str, None] = "0022_user_auth_beta"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Postgres enum types declared once and re-used. create_type=False because the
# upgrade() body issues the CREATE TYPE explicitly (so Alembic won't try to
# auto-create/drop them per-column).
VIEW_TYPE = postgresql.ENUM(
    "event", "relative", "theme",
    name="view_type", create_type=False,
)
VIEW_STATUS = postgresql.ENUM(
    "open", "developing", "consensus", "resolved", "archived",
    name="view_status", create_type=False,
)
EXPRESSION_TIER = postgresql.ENUM(
    "conservative", "balanced", "aggressive",
    name="expression_tier", create_type=False,
)
EXPRESSION_KIND = postgresql.ENUM(
    "basket", "option_strategy", "pair", "multi_asset", "hedge",
    name="expression_kind", create_type=False,
)
CONFIDENCE_DIMENSION = postgresql.ENUM(
    "outcome", "expression",
    name="confidence_dimension", create_type=False,
)
EXPECTATION_SOURCE = postgresql.ENUM(
    "polymarket", "kalshi", "consensus", "model",
    name="expectation_source", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        # gen_random_uuid() lives in pgcrypto. Idempotent; 0001 already ran it.
        op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
        op.execute(
            "CREATE TYPE view_type AS ENUM ('event', 'relative', 'theme');"
        )
        op.execute(
            "CREATE TYPE view_status AS ENUM "
            "('open', 'developing', 'consensus', 'resolved', 'archived');"
        )
        op.execute(
            "CREATE TYPE expression_tier AS ENUM "
            "('conservative', 'balanced', 'aggressive');"
        )
        op.execute(
            "CREATE TYPE expression_kind AS ENUM "
            "('basket', 'option_strategy', 'pair', 'multi_asset', 'hedge');"
        )
        op.execute(
            "CREATE TYPE confidence_dimension AS ENUM ('outcome', 'expression');"
        )
        op.execute(
            "CREATE TYPE expectation_source AS ENUM "
            "('polymarket', 'kalshi', 'consensus', 'model');"
        )

    # ── market_views ────────────────────────────────────────────────────
    op.create_table(
        "market_views",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # NULL = curated / backend-generated view (the V1 default).
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("view_type", VIEW_TYPE, nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("thesis", sa.Text(), nullable=True),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("time_horizon", sa.Text(), nullable=True),
        sa.Column(
            "status",
            VIEW_STATUS,
            nullable=False,
            server_default=sa.text("'open'::view_status"),
        ),
        sa.Column("resolution_date", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_market_views_user_id", "market_views", ["user_id"])
    op.create_index("ix_market_views_status", "market_views", ["status"])
    op.create_index("ix_market_views_view_type", "market_views", ["view_type"])

    # ── view_expressions ────────────────────────────────────────────────
    op.create_table(
        "view_expressions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "view_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("market_views.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tier", EXPRESSION_TIER, nullable=False),
        sa.Column("expression_kind", EXPRESSION_KIND, nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("risk_profile", sa.Text(), nullable=True),
        sa.Column("capital_intensity", sa.Text(), nullable=True),
        sa.Column("historical_strength", sa.Text(), nullable=True),
        sa.Column("time_horizon", sa.Text(), nullable=True),
        # SOFT references (no FK) — cross-domain, see module docstring.
        sa.Column("backtest_run_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_view_expressions_view_id", "view_expressions", ["view_id"],
    )

    # ── view_transmission ───────────────────────────────────────────────
    op.create_table(
        "view_transmission",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "view_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("market_views.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "seq", sa.Integer(), nullable=False, server_default=sa.text("0"),
        ),
        sa.Column("from_node", sa.Text(), nullable=False),
        sa.Column("to_node", sa.Text(), nullable=False),
        sa.Column("edge_label", sa.Text(), nullable=True),
        sa.Column("strength", sa.Float(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_view_transmission_view_id", "view_transmission", ["view_id"],
    )

    # ── view_confidence ─────────────────────────────────────────────────
    op.create_table(
        "view_confidence",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "view_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("market_views.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dimension", CONFIDENCE_DIMENSION, nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "view_id", "dimension", name="uq_view_confidence_view_dimension",
        ),
    )
    op.create_index(
        "ix_view_confidence_view_id", "view_confidence", ["view_id"],
    )

    # ── view_expectations ───────────────────────────────────────────────
    op.create_table(
        "view_expectations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "view_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("market_views.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", EXPECTATION_SOURCE, nullable=False),
        sa.Column("market_id", sa.Text(), nullable=True),
        sa.Column("expected_value", sa.Float(), nullable=True),
        sa.Column("user_view_value", sa.Float(), nullable=True),
        sa.Column("surprise_sign", sa.Text(), nullable=True),
        sa.Column(
            "as_of",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("resolved_value", sa.Float(), nullable=True),
    )
    op.create_index(
        "ix_view_expectations_view_id", "view_expectations", ["view_id"],
    )

    # ── view_follows ────────────────────────────────────────────────────
    op.create_table(
        "view_follows",
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
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "user_id", "view_id", name="uq_view_follows_user_view",
        ),
    )
    op.create_index("ix_view_follows_user_id", "view_follows", ["user_id"])
    op.create_index("ix_view_follows_view_id", "view_follows", ["view_id"])


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index("ix_view_follows_view_id", table_name="view_follows")
    op.drop_index("ix_view_follows_user_id", table_name="view_follows")
    op.drop_table("view_follows")

    op.drop_index(
        "ix_view_expectations_view_id", table_name="view_expectations",
    )
    op.drop_table("view_expectations")

    op.drop_index("ix_view_confidence_view_id", table_name="view_confidence")
    op.drop_table("view_confidence")

    op.drop_index(
        "ix_view_transmission_view_id", table_name="view_transmission",
    )
    op.drop_table("view_transmission")

    op.drop_index(
        "ix_view_expressions_view_id", table_name="view_expressions",
    )
    op.drop_table("view_expressions")

    op.drop_index("ix_market_views_view_type", table_name="market_views")
    op.drop_index("ix_market_views_status", table_name="market_views")
    op.drop_index("ix_market_views_user_id", table_name="market_views")
    op.drop_table("market_views")

    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS expectation_source;")
        op.execute("DROP TYPE IF EXISTS confidence_dimension;")
        op.execute("DROP TYPE IF EXISTS expression_kind;")
        op.execute("DROP TYPE IF EXISTS expression_tier;")
        op.execute("DROP TYPE IF EXISTS view_status;")
        op.execute("DROP TYPE IF EXISTS view_type;")
