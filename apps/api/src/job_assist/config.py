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

    # Auth (feat/api-auth) — single shared bearer token gating every route
    # except /health. The frontend sends it via the Next.js server-side proxy
    # (token never reaches the browser); the GitHub Actions crons send it via
    # the API_AUTH_TOKEN secret. Empty = unconfigured → the middleware fails
    # OPEN with a loud log rather than bricking the app. ``auth_enforce`` flips
    # warn-only (log missing/invalid, allow through) → enforce (401). Roll out
    # by wiring every client to SEND the token in warn mode, verifying the
    # warn-logs, THEN flipping ``AUTH_ENFORCE=true``.
    api_auth_token: str = Field(default="")
    auth_enforce: bool = Field(default=False)

    # Database
    database_url: str = Field(default="postgresql+asyncpg://localhost/job_assist")

    # Supabase
    supabase_url: str = Field(default="")
    supabase_anon_key: str = Field(default="")
    supabase_service_role_key: str = Field(default="")

    # LLM APIs
    gemini_api_key: str = Field(default="")
    anthropic_api_key: str = Field(default="")

    # Company enrichment (PR #27) — logo.dev publishable token + Gemini
    # model id for the one-sentence description. The token is a public
    # client-side identifier; storing it in the DB-fronting service is OK.
    logo_dev_token: str = Field(default="")
    company_desc_model: str = Field(default="gemini-2.5-flash-lite")
    # After this many failures the sweep skips the row until /retry is
    # called explicitly. Keeps a flaky upstream from burning the whole
    # quota on the same dead handle every day.
    company_enrich_max_attempts: int = Field(default=3)

    # Division enrichment (PR #28b) — same model + cap defaults as the
    # company enrichment path. Kept as distinct settings so the operator
    # can tune them independently (e.g. raise the cap for divisions if a
    # particular Gemini quirk hits one but not the other).
    division_desc_model: str = Field(default="gemini-2.5-flash-lite")
    division_enrich_max_attempts: int = Field(default=3)

    # JD summarization (PR #41) — Flash Lite same as the other enrichment
    # paths. ``jd_summary_max_output_tokens`` caps cost per call; the
    # prompt asks for 100-200 words which sits ~250-400 tokens.
    jd_summary_model: str = Field(default="gemini-2.5-flash-lite")
    jd_summary_enrich_max_attempts: int = Field(default=3)
    jd_summary_max_output_tokens: int = Field(default=500)

    # Semantic embeddings (slice 1, feat/embeddings-slice1).
    # gemini-embedding-001 is the current GA text embedding model
    # (text-embedding-004 was shut down → 404 on embedContent). It defaults to
    # 3072 dims; we request output_dimensionality=embedding_dim (768) to match
    # the Vector(768) columns. The sweep gives up on a row after this many
    # failed attempts until /retry resets it, same cap pattern as the other
    # enrichment paths. NOTHING reads the vectors for ranking in slice 1.
    embedding_model: str = Field(default="gemini-embedding-001")
    embedding_dim: int = Field(default=768)
    embedding_enrich_max_attempts: int = Field(default=3)

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

    # Fantastic.jobs / Apify (feat/fantastic-jobs-ingest) — sources the curated
    # Workday/iCIMS employers whose boards block Railway's datacenter egress IP
    # (Apify's infra crawls them instead). Server-side ONLY (Railway env +
    # GitHub secret); never NEXT_PUBLIC_*. Empty = integration disabled: the
    # /admin/ingest/fantastic endpoint 503s loudly rather than calling Apify
    # with a "Bearer " (empty) token.
    apify_api_token: str = Field(default="")

    # Observability
    sentry_dsn: str = Field(default="")

    # LangSmith LLM tracing (Phase A4). Triple-gated OFF by default: the SDK
    # reads these from the environment directly (LANGSMITH_API_KEY /
    # LANGSMITH_TRACING / LANGSMITH_PROJECT). These typed fields exist only for
    # visibility (e.g. a health readout of whether tracing is configured) — the
    # key is set in Railway env only, never committed, and tracing stays OFF
    # unless ``langsmith_tracing`` is true AND the key is present.
    langsmith_api_key: str = Field(default="")
    langsmith_tracing: bool = Field(default=False)
    langsmith_project: str = Field(default="")


settings = Settings()
