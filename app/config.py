from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
    # K8s injects real env vars via envFrom → they override .env automatically.
    # .env is only used for local development and is git-ignored.

    # App
    secret_key: str = "dev-secret-key-change-in-production"
    # Discord ID of the admin user (set via env var ADMIN_DISCORD_ID)
    admin_discord_id: str = ""

    # Database
    database_url: str = "sqlite:////data/issuebell.db"

    # Discord
    discord_bot_token: str = ""
    discord_client_id: str = ""
    discord_client_secret: str = ""
    discord_redirect_uri: str = "http://localhost:8000/auth/callback"

    # GitHub OAuth App
    github_client_id: str = ""
    github_client_secret: str = ""
    github_redirect_uri: str = "http://localhost:8000/auth/github/callback"

    # GitHub webhook secret (must match the secret set on the GitHub webhook)
    github_webhook_secret: str = ""

    # Polling interval in seconds (default 3 min)
    poll_interval: int = 180


settings = Settings()
