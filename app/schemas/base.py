"""
Base schemas and common response models.
"""
from datetime import datetime
from typing import Generic, TypeVar, Optional, List
from uuid import UUID
from pydantic import BaseModel, ConfigDict


# Generic type for paginated responses
T = TypeVar("T")


class BaseSchema(BaseModel):
    """Base schema with common configuration."""

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
    )


class TimestampSchema(BaseSchema):
    """Schema mixin for timestamps."""

    created_at: datetime
    updated_at: datetime


class IDSchema(BaseSchema):
    """Schema mixin for UUID ID."""

    id: UUID


class PaginatedResponse(BaseSchema, Generic[T]):
    """Generic paginated response."""

    items: List[T]
    total: int
    page: int
    limit: int
    pages: int


class MessageResponse(BaseSchema):
    """Simple message response."""

    message: str


class ErrorResponse(BaseSchema):
    """Error response format."""

    error: dict

    class Error(BaseSchema):
        code: str
        message: str
        details: Optional[dict] = None
