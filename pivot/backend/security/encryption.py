"""Fernet symmetric encryption for at-rest secrets (Phase 0 audit fix).

The cipher is a process-wide singleton keyed on
``settings.kite_token_enc_key``. When the env var is empty the singleton
is ``None`` and callers must skip encryption — this is the dev path
where contributors don't want to manage a key.

All three encrypted columns in ``kite_sessions`` (``access_token``,
``request_token``, ``totp_secret``) are written through
:meth:`TokenCipher.encrypt` and read back through
:meth:`TokenCipher.decrypt`. ``decrypt`` is intentionally tolerant of
plaintext input so a partially-migrated DB still functions: rows that
predate this commit don't start with ``gAAAA`` and pass straight
through unchanged.
"""
from __future__ import annotations

import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from backend.config import settings

logger = logging.getLogger(__name__)

# Fernet tokens are urlsafe-base64; v1 tokens always begin with this
# prefix because the version byte (0x80) base64-encodes to "gAAAA".
_FERNET_PREFIX = "gAAAA"


class TokenCipher:
    """Thin wrapper around :class:`cryptography.fernet.Fernet`.

    Encryption and decryption both pass ``None`` through unchanged so
    callers don't need to special-case nullable columns. Decryption is
    additionally tolerant of legacy plaintext values that pre-date
    encryption — those are detected via the absence of the Fernet
    version prefix and returned verbatim.
    """

    def __init__(self, key: bytes | str) -> None:
        if isinstance(key, str):
            key_bytes = key.encode("utf-8")
        else:
            key_bytes = key
        if not key_bytes:
            raise ValueError("TokenCipher key must be non-empty")
        try:
            self._fernet = Fernet(key_bytes)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "Invalid Fernet key — must be 32 url-safe base64-encoded bytes. "
                "Generate one with `python -c \"from cryptography.fernet import "
                "Fernet; print(Fernet.generate_key().decode())\"`."
            ) from exc

    def encrypt(self, plaintext: str | None) -> str | None:
        """Encrypt a string; ``None`` passes through; empty string round-trips."""
        if plaintext is None:
            return None
        if plaintext == "":
            return ""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str | None) -> str | None:
        """Decrypt a Fernet token; pass plaintext / ``None`` / ``""`` through.

        Returns the original value untouched when ``ciphertext`` lacks
        the Fernet prefix — that case represents a legacy plaintext row
        written before encryption was enabled, and we want reads to
        keep succeeding through the migration window.
        """
        if ciphertext is None:
            return None
        if ciphertext == "":
            return ""
        if not self.is_encrypted(ciphertext):
            return ciphertext
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            # Looked like Fernet output but didn't validate — most
            # likely a key rotation or DB-copy mismatch. Surface a
            # clear error rather than silently returning ciphertext.
            raise ValueError(
                "Failed to decrypt token — KITE_TOKEN_ENC_KEY does not match "
                "the key that encrypted this row."
            ) from exc

    @staticmethod
    def is_encrypted(value: str | None) -> bool:
        """Cheap sniff: does ``value`` look like a Fernet v1 token?"""
        if not value:
            return False
        return value.startswith(_FERNET_PREFIX)


_cipher_singleton: Optional[TokenCipher] = None
_cipher_loaded: bool = False


def get_cipher() -> TokenCipher | None:
    """Return the process-wide :class:`TokenCipher`, or ``None`` in dev mode.

    Reads ``settings.kite_token_enc_key`` once on first call. An empty
    key means encryption is disabled (legitimate dev path) and callers
    should skip the encrypt / decrypt steps.
    """
    global _cipher_singleton, _cipher_loaded
    if _cipher_loaded:
        return _cipher_singleton
    key = settings.token_enc_key
    if not key:
        logger.info(
            "KITE_TOKEN_ENC_KEY is empty; broker tokens will be stored in "
            "plaintext (dev mode). Set a key to enable at-rest encryption."
        )
        _cipher_singleton = None
    else:
        _cipher_singleton = TokenCipher(key)
        logger.info("TokenCipher initialized; broker tokens encrypted at rest.")
    _cipher_loaded = True
    return _cipher_singleton


def generate_key() -> str:
    """CLI helper: return a fresh urlsafe-base64 Fernet key as a str."""
    return Fernet.generate_key().decode("ascii")
