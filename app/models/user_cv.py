"""
UserCV model - stores user uploaded CVs.
"""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional, List
from sqlalchemy import String, Boolean, Text, Integer, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.user_skill import UserSkill


class UserCV(BaseModel):
    """
    User CV entity.

    Stores metadata about uploaded CVs.
    Actual files are stored encrypted on disk/S3.
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
    file_path: Mapped[str] = mapped_column(Text, nullable=False)  # Encrypted storage path
    file_hash: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
    )  # SHA-256 for deduplication
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Processing status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    processed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="cvs")
    skills: Mapped[List["UserSkill"]] = relationship(
        "UserSkill",
        back_populates="cv",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<UserCV {self.filename} for user_id={self.user_id}>"
