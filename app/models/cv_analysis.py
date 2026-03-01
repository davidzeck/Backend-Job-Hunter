"""
CVAnalysis model — caches CV-vs-JD analysis results for 24 hours.

Each row represents one analysis of a specific CV against a specific job.
Results are cached to avoid re-calling OpenAI for repeated views.
Expired rows can be cleaned up via a periodic task using the expires_at index.
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Float, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class CVAnalysis(BaseModel):
    __tablename__ = "cv_analyses"

    # Foreign Keys
    cv_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user_cvs.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    # Analysis results
    match_score: Mapped[float] = mapped_column(Float, nullable=False)
    present_keywords: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    missing_keywords: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    suggested_additions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # Snapshot of JD keywords at analysis time (detect if JD changed)
    jd_keywords_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Cache timestamps
    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        # Fast cache lookup: "has this CV been analyzed against this job?"
        Index("ix_cv_analyses_cv_job", "cv_id", "job_id"),
        # Fast cleanup of expired analyses
        Index("ix_cv_analyses_expires", "expires_at"),
    )

    def __repr__(self) -> str:
        return f"<CVAnalysis cv={self.cv_id} job={self.job_id} score={self.match_score}>"
