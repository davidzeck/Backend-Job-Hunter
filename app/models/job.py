"""
Job model - the core entity representing a job posting.
"""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional, List
from sqlalchemy import String, Boolean, Text, Integer, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.company import Company
    from app.models.job_source import JobSource
    from app.models.job_skill import JobSkill
    from app.models.user_job_alert import UserJobAlert


class Job(BaseModel):
    """
    Job posting entity.

    Each job is discovered from a specific source and belongs to a company.
    Jobs are deduplicated by (source_id, external_id).
    """

    __tablename__ = "jobs"

    # Unique constraint for deduplication
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_job_source_external"),
    )

    # Foreign Keys
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_sources.id"),
        nullable=False,
        index=True,
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id"),
        nullable=False,
        index=True,
    )

    # Identification
    external_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )  # ID from the source system

    # Core fields
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    location_type: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )  # 'onsite', 'remote', 'hybrid'
    job_type: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )  # 'full_time', 'contract', 'internship'
    seniority_level: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )  # 'entry', 'mid', 'senior', 'lead'

    # Application
    apply_url: Mapped[str] = mapped_column(Text, nullable=False)

    # Salary (optional)
    salary_min: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    salary_max: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    salary_currency: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    # Timestamps
    posted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # Raw data storage (for debugging)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Relationships
    company: Mapped["Company"] = relationship("Company", back_populates="jobs")
    source: Mapped["JobSource"] = relationship("JobSource", back_populates="jobs")
    skills: Mapped[List["JobSkill"]] = relationship(
        "JobSkill",
        back_populates="job",
        cascade="all, delete-orphan",
    )
    alerts: Mapped[List["UserJobAlert"]] = relationship(
        "UserJobAlert",
        back_populates="job",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Job {self.title} at {self.company_id}>"
