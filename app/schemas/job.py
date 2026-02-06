"""
Job schemas.
"""
from datetime import datetime
from typing import Optional, List
from uuid import UUID
from app.schemas.base import BaseSchema, TimestampSchema, IDSchema


class CompanyBrief(BaseSchema):
    """Brief company info for job responses."""

    id: UUID
    name: str
    slug: str
    logo_url: Optional[str] = None


class JobSkillResponse(BaseSchema):
    """Job skill response."""

    skill_name: str
    skill_category: Optional[str] = None
    is_required: bool
    min_years_experience: Optional[int] = None


class JobBase(BaseSchema):
    """Base job schema."""

    title: str
    location: Optional[str] = None
    location_type: Optional[str] = None
    job_type: Optional[str] = None
    apply_url: str


class JobListItem(JobBase, IDSchema):
    """Job list item (minimal info for lists)."""

    company: CompanyBrief
    posted_at: Optional[datetime] = None
    discovered_at: datetime
    is_active: bool


class JobDetail(JobListItem, TimestampSchema):
    """Full job detail."""

    description: Optional[str] = None
    seniority_level: Optional[str] = None
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    salary_currency: Optional[str] = None
    expires_at: Optional[datetime] = None
    skills: List[JobSkillResponse] = []


class JobFilters(BaseSchema):
    """Job filtering parameters."""

    company: Optional[List[str]] = None  # Company slugs
    location: Optional[str] = None
    role: Optional[str] = None  # Search in title
    location_type: Optional[str] = None  # 'remote', 'onsite', 'hybrid'
    days_ago: int = 7


class SkillMatch(BaseSchema):
    """Matching skill in skill gap analysis."""

    skill_name: str
    user_level: Optional[str] = None
    required_level: Optional[str] = None


class MissingSkill(BaseSchema):
    """Missing skill in skill gap analysis."""

    skill_name: str
    is_required: bool
    category: Optional[str] = None


class PartialSkill(BaseSchema):
    """Partial skill match in skill gap analysis."""

    skill_name: str
    user_years: Optional[float] = None
    required_years: Optional[int] = None
    gap: str = "Need more experience"


class SkillGapResponse(BaseSchema):
    """Skill gap analysis response."""

    job_id: UUID
    job_title: str
    matching_skills: List[SkillMatch] = []
    missing_skills: List[MissingSkill] = []
    partial_skills: List[PartialSkill] = []
    match_percentage: float
    recommendation: str
