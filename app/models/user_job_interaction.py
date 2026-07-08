"""
UserJobInteraction model - a user's manual actions on a browsed job.

Separate from UserJobAlert on purpose: alerts are the notifications/matches feed
(the product's core differentiator). Browse-view Save/Applied must not pollute
that feed or its counts, so they live here, keyed by (user_id, job_id).
"""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from sqlalchemy import Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.job import Job


class UserJobInteraction(BaseModel):
    """One row per (user, job): whether the user saved and/or applied to it."""

    __tablename__ = "user_job_interactions"

    # One interaction row per user per job
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_user_job_interaction"),
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

    # User actions
    saved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    saved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    applied_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships (one-directional — reverse nav not needed; repo queries directly)
    user: Mapped["User"] = relationship("User")
    job: Mapped["Job"] = relationship("Job")

    def __repr__(self) -> str:
        return f"<UserJobInteraction user_id={self.user_id} job_id={self.job_id} saved={self.saved} applied={self.applied}>"
