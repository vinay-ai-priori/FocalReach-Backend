from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Application
    APP_NAME: str = "FocalReach Outbound Engine"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    API_V1_PREFIX: str = "/api/v1"
    CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Database
    DATABASE_URL: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/focalreach"

    # Rate limiting (per client IP, one-minute windows)
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_PER_MINUTE: int = 120
    RATE_LIMIT_AUTH_PER_MINUTE: int = 10

    # Redis / Celery
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    # Cosine-similarity floor for the semantic column-matching tier (0-1).
    SEMANTIC_MATCH_THRESHOLD: float = 0.55
    # Cosine-similarity floor for a lead title to count as EXACTLY one of the ICP's
    # target roles (full role score). Deliberately strict — paraphrases of the same
    # role pass ("VP of Ops" ~ "Vice President Operations"); adjacent roles don't.
    ROLE_MATCH_THRESHOLD: float = 0.80
    AI_CACHE_TTL_SECONDS: int = 7 * 24 * 3600

    # Website intelligence
    WEBSITE_CACHE_TTL_SECONDS: int = 7 * 24 * 3600
    CRAWLER_TIMEOUT_SECONDS: int = 20
    CRAWLER_USER_AGENT: str = "FocalReachBot/1.0 (+https://focalreach.ai)"
    CRAWLER_MAX_PAGES: int = 5
    MIN_CONTENT_LENGTH_FOR_PLAYWRIGHT_FALLBACK: int = 400

    # Calendar
    CALCOM_BOOKING_URL: str = "https://cal.com/your-team/discovery-call"

    # Auth
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_MINUTES: int = 15
    REFRESH_TOKEN_DAYS: int = 7
    LOGIN_MAX_ATTEMPTS: int = 5
    LOGIN_LOCKOUT_SECONDS: int = 900
    SUPERADMIN_EMAIL: str = ""
    SUPERADMIN_PASSWORD: str = ""
    SUPERADMIN_NAME: str = "App Owner"

    # Mailbox connections (IMAP/SMTP app passwords) — symmetric encryption key for
    # credentials at rest. Generate with: python -c "from cryptography.fernet import
    # Fernet; print(Fernet.generate_key().decode())". Required in production; a
    # per-process fallback key is used in dev so the app still runs without one set
    # (existing encrypted rows become unreadable if the key ever changes).
    MAILBOX_CREDENTIALS_KEY: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
