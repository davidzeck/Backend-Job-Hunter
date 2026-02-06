"""
Company schemas.
"""
from typing import Optional
from uuid import UUID
from app.schemas.base import BaseSchema, TimestampSchema, IDSchema


class CompanyBase(BaseSchema):
    """Base company schema."""

    name: str
    slug: str
    careers_url: Optional[str] = None
    logo_url: Optional[str] = None
    description: Optional[str] = None


class CompanyCreate(CompanyBase):
    """Company creation schema."""

    pass


class CompanyUpdate(BaseSchema):
    """Company update schema."""

    name: Optional[str] = None
    careers_url: Optional[str] = None
    logo_url: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class CompanyResponse(CompanyBase, IDSchema, TimestampSchema):
    """Company response schema."""

    is_active: bool
    jobs_count: int = 0
    sources_count: int = 0
