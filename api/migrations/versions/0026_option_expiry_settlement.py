"""Allow 'expired' as an option-strategy status — for paper cash-settlement.

The paper option-expiry settlement job (backend/paper/option_settlement.py)
cash-settles an ACTIVE strategy's legs at intrinsic value on expiry and flips
it to a new terminal status 'expired' (distinct from 'closed', which means a
user squared it off). Widen the ck_option_strategies_status CHECK constraint
to admit it. No data migration — no existing row uses the value yet.

Revision ID: 0026_option_expiry_settlement
Revises: 0025_fractional_quantity
"""
from typing import Union

from alembic import op

revision: str = "0026_option_expiry_settlement"
down_revision: Union[str, None] = "0025_fractional_quantity"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_option_strategies_status"
_TABLE = "option_strategies"

_OLD = (
    "status IN ('registered', 'withdrawn', 'intent_armed', "
    "'active', 'closed', 'rejected', 'blocked')"
)
_NEW = (
    "status IN ('registered', 'withdrawn', 'intent_armed', "
    "'active', 'closed', 'expired', 'rejected', 'blocked')"
)


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _NEW)


def downgrade() -> None:
    # Fold any settled rows back to 'closed' so the tighter constraint holds.
    op.execute(
        "UPDATE option_strategies SET status = 'closed' WHERE status = 'expired'"
    )
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _OLD)
