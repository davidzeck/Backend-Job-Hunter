"""
Application configuration using Pydantic Settings.
All configuration is loaded from environment variables.
"""
from functools import lru_cache
from typing import List, Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Secrets that must never be used outside development
_DEV_ONLY_DEFAULTS = {
    "secret_key": "your-super-secret-key-change-in-production",
    "s3_aws_access_key_id": "minioadmin",
    "s3_aws_secret_access_key": "minioadmin",
}


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application
    app_name: str = "Job Scout API"
    app_version: str = "1.0.0"
    debug: bool = False
    environment: str = "development"  # development, staging, production

    # API
    api_prefix: str = "/api/v1"
    allowed_hosts: List[str] = ["*"]
    cors_origins: List[str] = ["http://localhost:3000", "http://localhost:3001", "http://localhost:8000"]

    # Database
    database_url: str = "postgresql+asyncpg://jobscout:jobscout@localhost:5432/jobscout"
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # JWT Authentication
    secret_key: str = "your-super-secret-key-change-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # Firebase Cloud Messaging
    fcm_credentials_path: Optional[str] = None

    # Email
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from_email: str = "noreply@jobscout.com"

    # Scraping
    scrape_user_agent: str = "JobScout/1.0 (+https://jobscout.com)"
    scrape_timeout_seconds: int = 30
    scrape_rate_limit_per_minute: int = 10

    # File Storage (legacy local dir — kept for backward compat during transition)
    upload_dir: str = "./uploads"
    max_cv_size_mb: int = 5

    # S3 / MinIO Object Storage
    s3_endpoint_url: Optional[str] = None   # None = real AWS; "http://localhost:9000" = MinIO
    s3_bucket_name: str = "jobscout-cvs"
    s3_aws_access_key_id: str = "minioadmin"
    s3_aws_secret_access_key: str = "minioadmin"
    s3_region: str = "us-east-1"
    s3_presign_upload_expires: int = 900    # 15 min — client must upload within this window
    s3_presign_download_expires: int = 3600  # 1 h — download link TTL

    # OpenAI (AI/ATS layer)
    openai_api_key: Optional[str] = None
    openai_embedding_model: str = "text-embedding-3-small"
    openai_chat_model: str = "gpt-4o-mini"
    openai_max_tokens_analysis: int = 1500
    openai_max_tokens_tailor: int = 2000

    @property
    def max_cv_size_bytes(self) -> int:
        return self.max_cv_size_mb * 1024 * 1024

    @model_validator(mode="after")
    def _reject_dev_secrets_in_production(self) -> "Settings":
        """Fail loud if production/staging still uses dev-only default secrets."""
        if self.environment in ("production", "staging"):
            for field_name, dev_default in _DEV_ONLY_DEFAULTS.items():
                actual = getattr(self, field_name)
                if actual == dev_default:
                    raise ValueError(
                        f"SECURITY: '{field_name}' is still set to its development default. "
                        f"Set a real value via environment variable in {self.environment}."
                    )
        return self


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
