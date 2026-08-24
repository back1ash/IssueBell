"""Authenticated encryption for OAuth tokens stored in the database.

Existing plaintext values are accepted only long enough to migrate them at
application startup. New writes are always encrypted with Fernet.
"""

from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Engine, Text, text
from sqlalchemy.types import TypeDecorator

from app.config import settings


logger = logging.getLogger(__name__)
TOKEN_PREFIX = "fernet:v1:"


class TokenDecryptionError(ValueError):
    """Raised when a stored token cannot be decrypted with the configured key."""


def _fernet() -> Fernet:
    # A dedicated key is preferred, while deriving from SECRET_KEY keeps
    # existing deployments upgrade-compatible. SHA-256 produces the exact
    # 32-byte material required by Fernet after URL-safe base64 encoding.
    material = getattr(settings, "token_encryption_key", "") or settings.secret_key
    key = base64.urlsafe_b64encode(hashlib.sha256(material.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_token(value: str | None) -> str | None:
    if not value or value.startswith(TOKEN_PREFIX):
        return value
    encrypted = _fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return f"{TOKEN_PREFIX}{encrypted}"


def decrypt_token(value: str | None) -> str | None:
    if not value or not value.startswith(TOKEN_PREFIX):
        # Legacy plaintext is supported during the one-time startup migration.
        return value
    payload = value.removeprefix(TOKEN_PREFIX)
    try:
        return _fernet().decrypt(payload.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError) as exc:
        raise TokenDecryptionError(
            "Stored GitHub token cannot be decrypted; reconnect GitHub."
        ) from exc


class EncryptedToken(TypeDecorator[str]):
    """SQLAlchemy string type that encrypts values at rest transparently."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> str | None:  # noqa: ANN001
        return encrypt_token(value)

    def process_result_value(self, value: str | None, dialect) -> str | None:  # noqa: ANN001
        return decrypt_token(value)


def migrate_plaintext_tokens(engine: Engine) -> int:
    """Encrypt legacy plaintext GitHub tokens in place after schema creation."""

    migrated = 0
    with engine.begin() as connection:
        rows = connection.execute(
            text("SELECT id, github_token FROM users WHERE github_token IS NOT NULL")
        ).mappings()
        for row in rows:
            token = row["github_token"]
            if token and not token.startswith(TOKEN_PREFIX):
                connection.execute(
                    text("UPDATE users SET github_token = :token WHERE id = :user_id"),
                    {"token": encrypt_token(token), "user_id": row["id"]},
                )
                migrated += 1
    if migrated:
        logger.info("Encrypted %s legacy GitHub OAuth token(s) at rest", migrated)
    return migrated
