"""Application configuration loaded from environment variables."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings. Loaded from .env file or environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Environment
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")

    # Server
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)
    cors_origins: list[str] = Field(default=["http://localhost:3000"])

    # Database
    database_url: str = Field(default="postgresql+asyncpg://localhost/job_assist")

    # Supabase
    supabase_url: str = Field(default="")
    supabase_anon_key: str = Field(default="")
    supabase_service_role_key: str = Field(default="")

    # LLM APIs
    gemini_api_key: str = Field(default="")
    anthropic_api_key: str = Field(default="")

    # Gmail
    gmail_credentials_path: str = Field(default="./credentials.json")
    gmail_token_path: str = Field(default="./token.json")
    # Production path: paste the OAuth client JSON contents as a string into
    # GMAIL_CREDENTIALS_JSON and the long-lived refresh token into
    # GMAIL_REFRESH_TOKEN. Both default empty so the API still boots without
    # them; the /admin/gmail/backfill endpoint surfaces a clear 503 if either
    # is missing at request time.
    gmail_credentials_json: str = Field(default="")
    gmail_refresh_token: str = Field(default="")

    # Email
    resend_api_key: str = Field(default="")
    digest_from_email: str = Field(default="")
    digest_to_email: str = Field(default="")

    # Aggregator
    jsearch_api_key: str = Field(default="")

    # Observability
    sentry_dsn: str = Field(default="")


settings = Settings()
