"""
User schemas.
"""
from datetime import datetime
from typing import Optional, List
from uuid import UUID
from pydantic import EmailStr, Field
from app.schemas.base import BaseSchema, TimestampSchema, IDSchema


class UserPreferences(BaseSchema):
    """User preferences schema."""

    roles: List[str] = Field(
        default=[
            "software_engineer",
            "fullstack_engineer",
            "backend_engineer",
            "frontend_engineer",
        ]
    )
    locations: List[str] = Field(default=["kenya", "remote"])
    companies: List[str] = Field(default=[])  # Empty = all companies

    class NotificationPreferences(BaseSchema):
        push: bool = True
        email: bool = True
        frequency: str = "immediate"  # 'immediate', 'daily', 'weekly'

    notifications: NotificationPreferences = Field(
        default_factory=NotificationPreferences
    )


class UserBase(BaseSchema):
    """Base user schema."""

    email: EmailStr
    full_name: Optional[str] = None
    phone: Optional[str] = None


class UserCreate(UserBase):
    """User creation schema."""

    password: str = Field(..., min_length=8)


class UserUpdate(BaseSchema):
    """User update schema."""

    full_name: Optional[str] = None
    phone: Optional[str] = None
    preferences: Optional[UserPreferences] = None


class UserResponse(UserBase, IDSchema, TimestampSchema):
    """User response schema."""

    email_verified: bool
    is_active: bool
    is_admin: bool = False
    preferences: dict
    last_seen_at: Optional[datetime] = None


class UserProfileResponse(UserResponse):
    """Full user profile response."""

    skills_count: int = 0
    has_cv: bool = False


class UpdateFCMTokenRequest(BaseSchema):
    """Update FCM token request."""

    fcm_token: str
