"""
JobSource model - the specific sources we scrape for jobs.
Each company can have multiple sources (careers page, Indeed, Glassdoor, etc.)
"""
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional, List
from sqlalchemy import String, Boolean, Text, Integer, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.company import Company
    from app.models.job import Job
    from app.models.scrape_log import ScrapeLog


class JobSource(BaseModel):
    """
    Job source entity.

    Represents a specific source URL that we scrape for jobs.
    One company can have multiple sources (e.g., Google Careers page + Indeed listings).
    """

    __tablename__ = "job_sources"

    # Foreign Keys
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id"),
        nullable=False,
        index=True,
    )

    # Fields
    source_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )  # 'careers_page', 'indeed', 'glassdoor'

    url: Mapped[str] = mapped_column(Text, nullable=False)
    scraper_class: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )  # Python class name for the scraper

    # Scheduling
    scrape_interval_minutes: Mapped[int] = mapped_column(Integer, default=30)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # Health tracking
    last_scraped_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_success_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    health_status: Mapped[str] = mapped_column(
        String(20),
        default="unknown",
    )  # 'healthy', 'degraded', 'failing', 'unknown'
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)

    # Scraper-specific configuration (flexible JSON)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)

    # Relationships
    company: Mapped["Company"] = relationship("Company", back_populates="sources")
    jobs: Mapped[List["Job"]] = relationship(
        "Job",
        back_populates="source",
        cascade="all, delete-orphan",
    )
    scrape_logs: Mapped[List["ScrapeLog"]] = relationship(
        "ScrapeLog",
        back_populates="source",
        cascade="all, delete-orphan",
    )

    def mark_success(self, jobs_found: int, new_jobs: int) -> None:
        """Mark a successful scrape."""
        now = datetime.now(timezone.utc)
        self.last_scraped_at = now
        self.last_success_at = now
        self.consecutive_failures = 0
        self.health_status = "healthy"

    def mark_failure(self, error: str) -> None:
        """Mark a failed scrape."""
        self.last_scraped_at = datetime.now(timezone.utc)
        self.consecutive_failures += 1

        if self.consecutive_failures >= 3:
            self.health_status = "failing"
            self.is_active = False  # Auto-disable after 3 failures
        elif self.consecutive_failures >= 1:
            self.health_status = "degraded"

    def __repr__(self) -> str:
        return f"<JobSource {self.source_type} for company_id={self.company_id}>"
