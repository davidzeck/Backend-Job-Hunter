"""
CVDraft model — an AI-curated CV rewrite awaiting user review/approval.

One LIVE draft per (cv, job): a new curate supersedes the previous draft.
Documents (DOCX/PDF) are only ever rendered from an APPROVED draft — the human
review gate sits between the AI output and any downloadable file.
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel

# Draft status machine:
#   generating → review → approved → rendered
#        └──────────┴────────┴──▶ failed
#   any live status ──▶ superseded (a newer curate for the same cv+job)
DRAFT_STATUS_GENERATING = "generating"
DRAFT_STATUS_REVIEW = "review"
DRAFT_STATUS_APPROVED = "approved"
DRAFT_STATUS_RENDERED = "rendered"
DRAFT_STATUS_FAILED = "failed"
DRAFT_STATUS_SUPERSEDED = "superseded"


class CVDraft(BaseModel):
    __tablename__ = "cv_drafts"

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
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # {original: CVStructure, tailored: CVStructure, keywords_injected: [str]}
    content: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=DRAFT_STATUS_GENERATING,
        index=True,
    )
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # sanitized

    # Rendered artifacts (set by generate_cv_document)
    docx_s3_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pdf_s3_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # "my drafts" listing + live-draft lookup for supersede
        Index("ix_cv_drafts_user_status", "user_id", "status"),
        Index("ix_cv_drafts_cv_job", "cv_id", "job_id"),
    )

    def __repr__(self) -> str:
        return f"<CVDraft cv={self.cv_id} job={self.job_id} status={self.status}>"
