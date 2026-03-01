"""
UserCV model - stores user uploaded CVs.

Upload status state machine:
  pending_upload → uploaded → processing → ready
                                         ↓
                                       failed  (retryable, max 2 retries via Celery)
"""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional, List
from sqlalchemy import String, Boolean, Text, Integer, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.user_skill import UserSkill
    from app.models.cv_chunk import CVChunk
    from app.models.cv_analysis import CVAnalysis

# Valid upload_status values (referenced in service + tasks)
UPLOAD_STATUS_PENDING = "pending_upload"
UPLOAD_STATUS_UPLOADED = "uploaded"
UPLOAD_STATUS_PROCESSING = "processing"
UPLOAD_STATUS_READY = "ready"
UPLOAD_STATUS_FAILED = "failed"


class UserCV(BaseModel):
    """
    User CV entity.

    Stores metadata about uploaded CVs.
    Actual file bytes live in S3 (or MinIO for dev) — never on the API server disk.
    The s3_key column is the canonical S3 object key used by storage.py helpers.
    """

    __tablename__ = "user_cvs"

    # Foreign Keys
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    # File info
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # s3_key is the canonical S3 object key (cvs/{prefix4}/{user_id}/{cv_id}/{filename})
    # file_path is kept for backward compatibility and set equal to s3_key on new uploads.
    file_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    s3_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_hash: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        index=True,  # fast deduplication lookup
    )  # SHA-256 hex digest for deduplication
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Upload state machine column
    upload_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=UPLOAD_STATUS_PENDING,
        index=True,
    )

    # Soft delete — DELETE endpoint sets is_active=False and removes S3 object
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # Set by Celery worker after pdfplumber + skill matching completes
    processed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Full extracted text cached here so analysis/tailoring doesn't re-download from S3
    full_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="cvs")
    skills: Mapped[List["UserSkill"]] = relationship(
        "UserSkill",
        back_populates="cv",
        cascade="all, delete-orphan",
    )
    chunks: Mapped[List["CVChunk"]] = relationship(
        "CVChunk",
        back_populates="cv",
        cascade="all, delete-orphan",
    )
    analyses: Mapped[List["CVAnalysis"]] = relationship(
        "CVAnalysis",
        cascade="all, delete-orphan",
    )

    # Composite index: fast "list active CVs for this user" query
    __table_args__ = (
        Index("ix_user_cvs_user_active", "user_id", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<UserCV {self.filename} status={self.upload_status} user_id={self.user_id}>"
