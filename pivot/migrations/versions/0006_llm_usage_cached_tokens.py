"""Add cached_input_tokens column to llm_usage.

Phase 1 — Wave 3a. OpenAI's Responses API reports a
``input_tokens_details.cached_tokens`` value when the cached prefix of a
prompt is served from cache. Those tokens are billed at 50% of the
normal input rate, so without breaking them out we over-bill on every
warm-cache turn. We need correct numbers BEFORE we ship the Phase 1
prompt cuts so the before / after comparison is honest.

Backfill is intentionally not attempted: any row created before this
migration shipped has no cache-token data to recover, so the column
defaults to 0 (i.e. treat all input tokens as full-priced — matches the
behavior the row was costed at).

The ``server_default='0'`` is kept after the upgrade. New inserts from
the application path always pass ``cached_input_tokens`` explicitly
(default 0 from Python), but the DB-side default protects against any
straggler INSERT path (e.g. a future raw SQL script) and keeps backfill
trivial — there is no NULL state to deal with.

Revision ID: 0006_llm_usage_cached_tokens
Revises: 0005_llm_usage
Create Date: 2026-05-12
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0006_llm_usage_cached_tokens"
down_revision: Union[str, None] = "0005_llm_usage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOT NULL with server_default='0' so existing rows fill in cleanly
    # and we don't have to fight a NULL state in queries. The default is
    # left on the column after migration — see module docstring.
    op.add_column(
        "llm_usage",
        sa.Column(
            "cached_input_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("llm_usage", "cached_input_tokens")
