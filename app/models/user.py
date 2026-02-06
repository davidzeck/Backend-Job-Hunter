"""
User model - represents an application user.
"""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional, List
from sqlalchemy import String, Boolean, Text, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.user_cv import UserCV
    from app.models.user_skill import UserSkill
    from app.models.user_job_alert import UserJobAlert


# Default user preferences
DEFAULT_PREFERENCES = {
    "roles": [
        "software_engineer",
        "fullstack_engineer",
        "backend_engineer",
        "frontend_engineer",
    ],
    "locations": ["kenya", "remote"],
    "companies": [],  # Empty = all companies
    "notifications": {
        "push": True,
        "email": True,
        "frequency": "immediate",
    },
}


class User(BaseModel):
    """
    User entity.

    Stores user credentials, preferences, and notification settings.
    """

    __tablename__ = "users"

    # Authentication
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # Profile
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Push notifications
    fcm_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Status
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)

    # Preferences (stored as JSONB for flexibility)
    preferences: Mapped[dict] = mapped_column(
        JSONB,
        default=lambda: DEFAULT_PREFERENCES.copy(),
    )

    # Activity tracking
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    cvs: Mapped[List["UserCV"]] = relationship(
        "UserCV",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    skills: Mapped[List["UserSkill"]] = relationship(
        "UserSkill",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    alerts: Mapped[List["UserJobAlert"]] = relationship(
        "UserJobAlert",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<User {self.email}>"
