"""
Company model - the companies we monitor for jobs.
"""
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import String, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.job_source import JobSource
    from app.models.job import Job


class Company(BaseModel):
    """
    Company entity.

    For MVP, we have 5 hard-coded companies:
    - Google
    - Microsoft
    - Amazon
    - Deloitte
    - Safaricom
    """

    __tablename__ = "companies"

    # Fields
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    careers_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    logo_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    sources: Mapped[List["JobSource"]] = relationship(
        "JobSource",
        back_populates="company",
        cascade="all, delete-orphan",
    )
    jobs: Mapped[List["Job"]] = relationship(
        "Job",
        back_populates="company",
    )

    def __repr__(self) -> str:
        return f"<Company {self.name}>"
