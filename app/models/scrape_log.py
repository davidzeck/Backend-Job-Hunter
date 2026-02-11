"""
ScrapeLog model - audit trail for scraping operations.
"""
import uuid
from typing import TYPE_CHECKING, Optional
from sqlalchemy import String, Integer, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.job_source import JobSource


class ScrapeLog(BaseModel):
    """
    Scrape log entity.

    Records every scraping attempt for auditing and debugging.
    """

    __tablename__ = "scrape_logs"

    # Foreign Keys
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_sources.id"),
        nullable=False,
        index=True,
    )

    # Result
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )  # 'success', 'partial', 'failed'

    # Metrics
    jobs_found: Mapped[int] = mapped_column(Integer, default=0)
    new_jobs: Mapped[int] = mapped_column(Integer, default=0)
    updated_jobs: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Error details (if failed)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_traceback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Additional metadata (named extra_data because 'metadata' is reserved by SQLAlchemy)
    extra_data: Mapped[Optional[dict]] = mapped_column(
        "extra_data",
        JSONB,
        nullable=True,
    )  # Response codes, retries, etc.

    # Relationships
    source: Mapped["JobSource"] = relationship("JobSource", back_populates="scrape_logs")

    def __repr__(self) -> str:
        return f"<ScrapeLog {self.status} for source_id={self.source_id}>"
