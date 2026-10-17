"""Paper account lifecycle — get-or-create a user's single paper book.

Single-book-per-user (the user's P0 decision); the `label` column leaves
room for per-idea sub-accounts later. Seeding writes a 'seed' ledger row
so the account reconciles by replay from row zero.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models import PaperAccount, PaperLedgerEntry
from backend.paper.money import SEED_CAPITAL, Number, to_money


def get_or_create_account(
    db: Session,
    user_id: int,
    *,
    starting_capital: Optional[Number] = None,
) -> PaperAccount:
    """Return the user's paper account, creating + seeding it on first use.

    Idempotent and race-safe: the unique index on paper_accounts.user_id
    guarantees one book per user. We look up first; if a concurrent
    first-touch wins the insert, the SAVEPOINT rolls back only our losing
    insert (not the caller's other work) and we return the winner.
    ``starting_capital`` only applies on first creation.
    """
    uid = int(user_id)
    acct = (
        db.query(PaperAccount)
        .filter(PaperAccount.user_id == uid)
        .first()
    )
    if acct is not None:
        return acct

    if starting_capital is not None:
        seed = to_money(starting_capital)
    else:
        # Deployment-configurable seed (env PAPER_SEED_CAPITAL); falls back to
        # the SEED_CAPITAL constant if the setting is somehow unavailable.
        try:
            from backend.config import settings as _cfg
            seed = to_money(getattr(_cfg, "paper_seed_capital", None) or SEED_CAPITAL)
        except Exception:
            seed = SEED_CAPITAL
    acct = PaperAccount(
        user_id=uid,
        starting_capital=seed,
        cash_settled=seed,
        cash_available=seed,
        cash_reserved=to_money(0),
        mode="paper",
        is_active=True,
    )
    try:
        with db.begin_nested():
            db.add(acct)
            db.flush()
            db.add(PaperLedgerEntry(
                account_id=acct.id,
                kind="seed",
                amount=seed,
                balance_after=seed,
                note="initial paper capital",
            ))
            db.flush()
    except IntegrityError:
        # Lost the create race — return the winner.
        winner = (
            db.query(PaperAccount)
            .filter(PaperAccount.user_id == uid)
            .first()
        )
        if winner is not None:
            return winner
        raise
    return acct
