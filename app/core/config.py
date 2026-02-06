"""
Application configuration using Pydantic Settings.
All configuration is loaded from environment variables.
"""
from functools import lru_cache
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # File Storage
    upload_dir: str = "./uploads"
    max_cv_size_mb: int = 5


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
