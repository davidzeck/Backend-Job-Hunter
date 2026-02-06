"""
UserSkill model - skills extracted from user CVs.
"""
import uuid
from typing import TYPE_CHECKING, Optional
from sqlalchemy import String, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.user_cv import UserCV


class UserSkill(BaseModel):
    """
    User skill entity.

    Skills are extracted from CVs and stored for matching against job requirements.
    """

    __tablename__ = "user_skills"

    # Unique constraint: one skill per user
    __table_args__ = (
        UniqueConstraint("user_id", "skill_name", name="uq_user_skill"),
    )

    # Foreign Keys
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    cv_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user_cvs.id"),
        nullable=True,
    )

    # Skill info
    skill_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    skill_category: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )  # 'language', 'framework', 'tool', 'database', 'cloud'

    # Proficiency
    proficiency_level: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
    )  # 'beginner', 'intermediate', 'advanced', 'expert'
    years_experience: Mapped[Optional[float]] = mapped_column(
        Numeric(3, 1),
        nullable=True,
    )

    # Extraction metadata
    confidence_score: Mapped[Optional[float]] = mapped_column(
        Numeric(3, 2),
        nullable=True,
    )  # 0.00 to 1.00
    source: Mapped[str] = mapped_column(
        String(20),
        default="cv",
    )  # 'cv', 'manual', 'linkedin'

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="skills")
    cv: Mapped[Optional["UserCV"]] = relationship("UserCV", back_populates="skills")

    def __repr__(self) -> str:
        return f"<UserSkill {self.skill_name} for user_id={self.user_id}>"
