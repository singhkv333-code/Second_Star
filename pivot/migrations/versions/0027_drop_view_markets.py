"""Drop the View Markets tables — the opinion-markets direction is retired.

The belief -> expression -> deployment layer added by 0023/0024 was retired on
2026-09-05 (see CLAUDE.md section 4, where "no opinion markets" is now a named
non-negotiable). `backend/view_markets/`, `routers/views.py`, the ORM models,
the Pydantic schemas and the FE "Opinions" tab all went with it.

The 107 rows these tables held were exported to
`pivot/archive/view_markets_2026_09_05/` BEFORE this migration ran — four of
them in `view_positions`, two of those owned by a real user. Read that folder's
README before assuming this is a clean loss.

Order matters: `view_positions` and `view_follows` carry FKs onto
`market_views` / `view_expressions`, and `view_expressions` onto `market_views`.
Children first, then parents, then the six ENUM types (no other table uses
them; `view_position_status` was declared as a CHECK constraint by 0024, not a
type, so there are six and not seven).

This migration is deliberately IRREVERSIBLE. `downgrade()` would have to
recreate six enums, seven tables and their indexes from a direction we have
committed to never shipping again; if you genuinely need them back,
`git revert` 0023/0024's deletion and re-run them rather than maintaining a
downgrade path for a dead feature.

Revision ID: 0027_drop_view_markets
Revises: 0026_option_expiry_settlement
"""
from typing import Union

from alembic import op

revision: str = "0027_drop_view_markets"
down_revision: Union[str, None] = "0026_option_expiry_settlement"
branch_labels = None
depends_on = None

# Children before parents.
_TABLES = (
    "view_positions",
    "view_follows",
    "view_expectations",
    "view_confidence",
    "view_transmission",
    "view_expressions",
    "market_views",
)

_ENUMS = (
    "view_type",
    "view_status",
    "expression_tier",
    "expression_kind",
    "confidence_dimension",
    "expectation_source",
)


def upgrade() -> None:
    # IF EXISTS throughout: a database that never ran 0023 (a fresh dev box,
    # or SQLite in the test suite) must not fail here.
    for table in _TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")

    # Postgres only — SQLite has no CREATE TYPE, and 0023 guarded its
    # creation the same way.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for enum in _ENUMS:
            op.execute(f"DROP TYPE IF EXISTS {enum};")


def downgrade() -> None:
    raise NotImplementedError(
        "0027_drop_view_markets is irreversible by design — the opinion-markets "
        "direction is retired. Data is archived at "
        "pivot/archive/view_markets_2026_09_05/."
    )
