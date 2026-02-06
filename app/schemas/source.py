"""
Job source schemas.
"""
from datetime import datetime
from typing import Optional, List
from uuid import UUID
from app.schemas.base import BaseSchema, TimestampSchema, IDSchema
from app.schemas.job import CompanyBrief


class SourceBase(BaseSchema):
    """Base source schema."""

    source_type: str
    url: str
    scraper_class: str
    scrape_interval_minutes: int = 30
    is_active: bool = True


class SourceCreate(SourceBase):
    """Source creation schema."""

    company_id: UUID
    config: dict = {}


class SourceUpdate(BaseSchema):
    """Source update schema."""

    url: Optional[str] = None
    scrape_interval_minutes: Optional[int] = None
    is_active: Optional[bool] = None
    config: Optional[dict] = None


class SourceResponse(SourceBase, IDSchema, TimestampSchema):
    """Source response schema."""

    company: CompanyBrief
    last_scraped_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    health_status: str
    consecutive_failures: int
    config: dict


class SourceHealthResponse(BaseSchema):
    """Source health summary."""

    id: UUID
    company_name: str
    source_type: str
    health_status: str
    last_success: Optional[datetime] = None
    consecutive_failures: int
    success_rate_7d: Optional[float] = None


class TriggerScrapeResponse(BaseSchema):
    """Response after triggering a manual scrape."""

    task_id: str
    message: str = "Scrape task queued"


class ScrapeLogResponse(IDSchema):
    """Scrape log response."""

    source_id: UUID
    status: str
    jobs_found: int
    new_jobs: int
    updated_jobs: int
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    created_at: datetime
