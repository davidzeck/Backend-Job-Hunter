"""
UserJobAlert model - tracks job notifications sent to users.
"""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from sqlalchemy import String, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.job import Job


class UserJobAlert(BaseModel):
    """
    User job alert entity.

    Tracks notifications sent to users about new jobs.
    Used for:
    - Preventing duplicate notifications
    - Tracking user engagement (read, saved, applied)
    """

    __tablename__ = "user_job_alerts"

    # Unique constraint: one alert per user per job
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_user_job_alert"),
    )

    # Foreign Keys
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id"),
        nullable=False,
        index=True,
    )

    # Notification tracking
    notified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    notification_channel: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
    )  # 'push', 'email', 'sms'
    is_delivered: Mapped[bool] = mapped_column(Boolean, default=False)

    # User engagement
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_saved: Mapped[bool] = mapped_column(Boolean, default=False)
    is_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    applied_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="alerts")
    job: Mapped["Job"] = relationship("Job", back_populates="alerts")

    def __repr__(self) -> str:
        return f"<UserJobAlert user_id={self.user_id} job_id={self.job_id}>"
