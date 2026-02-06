"""
Alert schemas.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID
from app.schemas.base import BaseSchema, IDSchema
from app.schemas.job import JobListItem


class AlertBase(BaseSchema):
    """Base alert schema."""

    is_read: bool = False
    is_saved: bool = False
    is_applied: bool = False


class AlertResponse(AlertBase, IDSchema):
    """Alert response schema."""

    job: JobListItem
    notified_at: datetime
    notification_channel: Optional[str] = None
    is_delivered: bool
    applied_at: Optional[datetime] = None


class AlertUpdate(BaseSchema):
    """Alert update schema."""

    is_read: Optional[bool] = None
    is_saved: Optional[bool] = None
    is_applied: Optional[bool] = None


class MarkReadRequest(BaseSchema):
    """Mark alert as read request."""

    pass  # Empty body, just PATCH /alerts/{id}/read


class MarkAppliedRequest(BaseSchema):
    """Mark alert as applied request."""

    pass  # Empty body, just PATCH /alerts/{id}/applied
