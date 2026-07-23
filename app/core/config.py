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
    CORS_ORIGINS: List[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://app.focalreach-ai.com",
    ]

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
    # Freshness window for the cross-campaign enrichment cache (global_companies).
    # Rows older than this are re-scraped and refreshed in place.
    ENRICHMENT_TTL_DAYS: int = 10
    # How many companies stage 2 of qualification (enrich + LLM ranking) processes in
    # parallel per wave. Sizing guide: each concurrent scrape adds ~50-100 MB (httpx
    # path) plus a shared Chromium (~300-500 MB) when JS fallbacks trigger; 5 keeps the
    # worker around ~1 GB peak while cutting import wall-clock ~4-4.5x vs sequential.
    QUALIFY_PARALLELISM: int = 5

    # Website intelligence
    WEBSITE_CACHE_TTL_SECONDS: int = 7 * 24 * 3600
    CRAWLER_TIMEOUT_SECONDS: int = 20
    CRAWLER_USER_AGENT: str = "FocalReachBot/1.0 (+https://focalreach.ai)"
    CRAWLER_MAX_PAGES: int = 5
    MIN_CONTENT_LENGTH_FOR_PLAYWRIGHT_FALLBACK: int = 400

    # Cal.com OAuth (per-user "Connect Calendar") — register an OAuth client in Cal.com
    # to get these. Redirect URI must be registered EXACTLY on the Cal.com side (path
    # included) and must point at the FRONTEND route (Cal.com Platform OAuth clients
    # only redirect to app URLs, not arbitrary backend endpoints) — the frontend then
    # calls POST /api/v1/calcom/exchange with the returned code to finish the flow.
    CALCOM_CLIENT_ID: str = ""
    CALCOM_CLIENT_SECRET: str = ""
    CALCOM_REDIRECT_URI: str = "http://localhost:5173/connect-calendar"
    CALCOM_OAUTH_AUTHORIZE_URL: str = "https://app.cal.com/auth/oauth2/authorize"
    CALCOM_OAUTH_TOKEN_URL: str = "https://api.cal.com/v2/auth/oauth2/token"
    CALCOM_API_BASE_URL: str = "https://api.cal.com/v2"
    # Cal.com scope names are RESOURCE_ACTION (per cal.com/docs/api-reference/v2/oauth),
    # e.g. "EVENT_TYPE_READ" not "READ_EVENT_TYPE" — and "Availability" in the dashboard
    # UI maps to the SCHEDULE_* scope, not AVAILABILITY_*. Must be a subset of what's
    # granted to the OAuth client (Platform dashboard -> OAuth Client -> permissions) or
    # the whole authorize request is rejected before Cal.com even shows a login screen.
    CALCOM_OAUTH_SCOPES: str = (
        "EVENT_TYPE_READ EVENT_TYPE_WRITE BOOKING_READ BOOKING_WRITE "
        "SCHEDULE_READ SCHEDULE_WRITE PROFILE_READ PROFILE_WRITE"
    )
    # How long before actual expiry we treat a Cal.com access token as due for refresh —
    # wide enough that a request never race-loses against the token dying mid-call.
    CALCOM_TOKEN_REFRESH_BUFFER_SECONDS: int = 300

    # Inbox reply poller (app/tasks/inbox_poll_tasks.py, every 10 min).
    INBOX_POLL_BATCH_SIZE: int = 50  # max new messages fetched per mailbox per poll
    # Below this, the intent classifier's own verdict is not trusted and the reply is
    # treated as neutral (paused + notified, no automated action) instead.
    REPLY_INTENT_CONFIDENCE_THRESHOLD: float = 0.6
    REPLY_DATETIME_CONFIDENCE_THRESHOLD: float = 0.6

    # Knowledge base uploads
    KNOWLEDGE_MAX_UPLOAD_MB: int = 25
    # Below this many extractable characters a document is treated as image-only/scanned
    # and flagged low_text instead of storing noise.
    KNOWLEDGE_MIN_TEXT_CHARS: int = 40

    # Object storage for original uploaded documents. STORAGE_MODE=local writes to
    # LOCAL_STORAGE_DIR on disk (no cloud creds needed); STORAGE_MODE=s3 uses the AWS_* vars.
    STORAGE_MODE: str = "local"  # "local" | "s3"
    LOCAL_STORAGE_DIR: str = "var/uploads"

    AWS_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_STORAGE_BUCKET_NAME: str = ""
    AWS_S3_PREFIX: str = "focalreach"
    # Optional custom endpoint for S3-compatible stores (MinIO etc.); blank for real AWS.
    AWS_S3_ENDPOINT_URL: str = ""

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
