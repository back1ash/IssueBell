from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )
    # K8s injects real env vars via envFrom → they override .env automatically.
    # .env is only used for local development and is git-ignored.

    # App
    # A predictable fallback would allow forged session cookies and admin
    # impersonation, so startup intentionally fails until a strong key is set.
    secret_key: str = Field(min_length=32)
    # Keep this stable when rotating the session secret. If omitted locally,
    # token encryption derives a key from SECRET_KEY for upgrade compatibility.
    token_encryption_key: str | None = Field(default=None, min_length=32)
    # Session cookies should only be sent over HTTPS in production.  Kept false
    # by default so a fresh local install still works on http://localhost.
    secure_cookies: bool = Field(
        default=False,
        validation_alias=AliasChoices("SECURE_COOKIES", "SESSION_HTTPS_ONLY"),
    )
    # Idle timeout, renewed on a dashboard visit (not background polling).
    session_max_age: int = Field(default=60 * 60 * 24 * 90, ge=300)
    session_absolute_max_age: int = Field(default=60 * 60 * 24 * 180, ge=300)
    # Comma-separated to keep environment configuration straightforward.
    allowed_hosts: str = "localhost,127.0.0.1,testserver"
    # Discord ID of the admin user (set via env var ADMIN_DISCORD_ID)
    admin_discord_id: str = ""

    # Database
    database_url: str = "sqlite:///./issuebell.db"

    # Discord
    discord_bot_token: str = ""
    discord_client_id: str = ""
    discord_client_secret: str = ""
    discord_redirect_uri: str = "http://localhost:8000/auth/callback"

    # GitHub OAuth App
    github_client_id: str = ""
    github_client_secret: str = ""
    github_redirect_uri: str = "http://localhost:8000/auth/github/callback"

    # OAuth requests must not leave a worker hanging indefinitely.
    oauth_http_timeout: float = Field(default=10.0, gt=0, le=60)
    oauth_state_max_age: int = Field(default=600, ge=60, le=1800)
    outbound_http_timeout: float = Field(default=15.0, gt=0, le=60)

    # Polling interval in seconds (default 3 min)
    poll_interval: int = Field(default=180, ge=30, le=3600)


settings = Settings()
