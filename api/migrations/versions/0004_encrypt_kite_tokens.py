"""Encrypt existing KiteSession tokens in-place.

Phase 0 audit fix: ``kite_sessions.access_token``,
``kite_sessions.request_token`` and ``kite_sessions.totp_secret`` were
written as plaintext. This migration re-encrypts existing rows with
Fernet using the key in ``settings.kite_token_enc_key``.

Behaviour:
  - If the env var is empty, log a warning and exit cleanly. We don't
    want devs without a key to be blocked from running `alembic upgrade`.
  - Rows that already start with the Fernet prefix (``gAAAA``) are
    skipped — re-running this migration is idempotent.
  - No schema change: the existing ``String(500)`` columns are wide
    enough for Fernet ciphertext.

``downgrade()`` is intentionally a no-op — reversing encryption would
write plaintext back to disk, which is the regression we're fixing.

Revision ID: 0004_encrypt_kite_tokens
Revises: 0003_multi_trigger
Create Date: 2026-05-12
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0004_encrypt_kite_tokens"
down_revision: Union[str, None] = "0003_multi_trigger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_FERNET_PREFIX = "gAAAA"


def _encrypt_existing_rows() -> None:
    """Walk kite_sessions and Fernet-encrypt any plaintext columns."""
    # Imported lazily so `alembic upgrade head` doesn't crash on a
    # machine without the cryptography lib in this venv.
    from backend.security.encryption import get_cipher

    cipher = get_cipher()
    if cipher is None:
        logger.warning(
            "0004_encrypt_kite_tokens: KITE_TOKEN_ENC_KEY is empty; "
            "leaving existing rows as plaintext. Set the env var and "
            "re-run this migration to encrypt at rest."
        )
        return

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, access_token, request_token, totp_secret "
            "FROM kite_sessions"
        )
    ).fetchall()

    updated = 0
    skipped = 0
    for row in rows:
        row_id = row[0]
        access_token = row[1]
        request_token = row[2]
        totp_secret = row[3]

        updates: dict[str, str] = {}
        if access_token and not access_token.startswith(_FERNET_PREFIX):
            enc = cipher.encrypt(access_token)
            if enc is not None:
                updates["access_token"] = enc
        if request_token and not request_token.startswith(_FERNET_PREFIX):
            enc = cipher.encrypt(request_token)
            if enc is not None:
                updates["request_token"] = enc
        if totp_secret and not totp_secret.startswith(_FERNET_PREFIX):
            enc = cipher.encrypt(totp_secret)
            if enc is not None:
                updates["totp_secret"] = enc

        if not updates:
            skipped += 1
            continue

        set_clause = ", ".join(f"{col} = :{col}" for col in updates)
        params = {**updates, "id": row_id}
        bind.execute(
            sa.text(f"UPDATE kite_sessions SET {set_clause} WHERE id = :id"),
            params,
        )
        updated += 1

    logger.info(
        "0004_encrypt_kite_tokens: encrypted %d row(s), skipped %d "
        "already-encrypted row(s).",
        updated,
        skipped,
    )


def upgrade() -> None:
    _encrypt_existing_rows()


def downgrade() -> None:
    # Intentional no-op: decrypting back to plaintext is the regression
    # this migration exists to fix. Rotate the key via a forward-only
    # migration if needed.
    pass
