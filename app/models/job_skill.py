"""
JobSkill model - skills required by a job posting.
"""
import uuid
from typing import TYPE_CHECKING, Optional
from sqlalchemy import String, Boolean, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.job import Job


class JobSkill(BaseModel):
    """
    Job skill requirement entity.

    Skills are extracted from job descriptions and used for matching against user skills.
    """

    __tablename__ = "job_skills"

    # Unique constraint: one skill per job
    __table_args__ = (
        UniqueConstraint("job_id", "skill_name", name="uq_job_skill"),
    )

    # Foreign Keys
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id"),
        nullable=False,
        index=True,
    )

    # Skill info
    skill_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    skill_category: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )  # 'language', 'framework', 'tool', 'database', 'cloud'

    # Requirements
    is_required: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )  # Required vs nice-to-have
    min_years_experience: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Relationships
    job: Mapped["Job"] = relationship("Job", back_populates="skills")

    def __repr__(self) -> str:
        return f"<JobSkill {self.skill_name} for job_id={self.job_id}>"
