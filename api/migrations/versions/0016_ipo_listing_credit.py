"""P3.1: IPO listing-credit columns on paper_ipo_allocations.

Three additive columns. No ALTER on any other table. Chains off 0015 which
introduced ``paper_ipo_allocations`` and the (already-nullable) P3.1
placeholders ``listing_price`` / ``simulated_pnl``. P3.1 wires the
listing-day credit and uses these columns to capture:

  book_credited   BOOLEAN NOT NULL DEFAULT FALSE
      Idempotency latch: ``True`` once the allotted shares have been
      credited into the paper book (or once terminally skipped because
      the paper account had insufficient buying power). Together with
      the UNIQUE ``paper_orders.client_request_id``
      (``ipo-listing-{allocation.id}``) this guarantees we can never
      double-credit a single allocation, even across retries / crashes.

  book_note       VARCHAR NULL
      Free-text note populated when the listing credit was attempted
      but skipped — e.g. ``"not credited: insufficient_buying_power"``.
      Always NULL on a successful credit. Surfaced on the Paper UI so
      the user can see exactly why a row is labelled "not credited".

  paper_fill_id   VARCHAR(36) NULL
      Soft reference to the ``paper_fills.id`` produced by the listing
      credit's MARKET BUY. NULL on a skipped credit. Not a hard FK for
      parity with the other paper-domain soft refs (workflow_id,
      conversation_id) — cross-dialect VARCHAR <-> UUID FKs are brittle.

NO data backfill is required: ``book_credited`` defaults to FALSE so
every pre-existing row simply waits for its ``listing_date`` to mature
and is then processed by the listing-credit poller. The poller is
listing-date-gated and idempotent, so old rows are safe regardless.

DEPLOY ORDER: must run after 0015 (which created the table). On a
brand-new Postgres DB this means base ORM tables + 0013 + 0014 + 0015
must have applied before reaching 0016.

Revision ID: 0016_ipo_listing_credit
Revises: 0015_paper_ipo_allocation
Create Date: 2026-06-02
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0016_ipo_listing_credit"
down_revision: Union[str, None] = "0015_paper_ipo_allocation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "paper_ipo_allocations",
        sa.Column(
            "book_credited", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "paper_ipo_allocations",
        sa.Column("book_note", sa.String(), nullable=True),
    )
    op.add_column(
        "paper_ipo_allocations",
        sa.Column("paper_fill_id", sa.String(36), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("paper_ipo_allocations", "paper_fill_id")
    op.drop_column("paper_ipo_allocations", "book_note")
    op.drop_column("paper_ipo_allocations", "book_credited")
