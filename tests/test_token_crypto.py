from sqlalchemy import create_engine, text

from app.token_crypto import (
    TOKEN_PREFIX,
    decrypt_token,
    encrypt_token,
    migrate_plaintext_tokens,
)


def test_token_encryption_round_trip_hides_plaintext():
    plaintext = "gho_example-secret-token"
    encrypted = encrypt_token(plaintext)

    assert encrypted is not None
    assert encrypted.startswith(TOKEN_PREFIX)
    assert plaintext not in encrypted
    assert decrypt_token(encrypted) == plaintext


def test_legacy_plaintext_is_temporarily_readable():
    assert decrypt_token("legacy-token") == "legacy-token"


def test_plaintext_migration_is_idempotent():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE users (id INTEGER PRIMARY KEY, github_token TEXT NULL)"
            )
        )
        connection.execute(
            text("INSERT INTO users (id, github_token) VALUES (1, :token)"),
            {"token": "legacy-token"},
        )

    assert migrate_plaintext_tokens(engine) == 1
    assert migrate_plaintext_tokens(engine) == 0

    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT github_token FROM users WHERE id = 1")
        ).scalar_one()
    assert stored.startswith(TOKEN_PREFIX)
    assert decrypt_token(stored) == "legacy-token"
