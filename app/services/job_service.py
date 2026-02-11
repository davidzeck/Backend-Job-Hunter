"""
Job service - business logic for job listing, detail, and skill gap analysis.
"""
from typing import List, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import JobNotFoundException
from app.models.user import User
from app.repositories.job_repository import JobRepository
from app.repositories.user_repository import UserRepository
from app.schemas.job import (
    JobListItem,
    JobDetail,
    CompanyBrief,
    JobSkillResponse,
    SkillGapResponse,
    SkillMatch,
    MissingSkill,
    PartialSkill,
)
from app.schemas.base import PaginatedResponse


class JobService:
    """Handles job search, detail retrieval, and skill analysis."""

    def __init__(self):
        self.job_repo = JobRepository()
        self.user_repo = UserRepository()

    async def list_jobs(
        self,
        db: AsyncSession,
        *,
        company_slugs: Optional[List[str]] = None,
        location: Optional[str] = None,
        role: Optional[str] = None,
        location_type: Optional[str] = None,
        days_ago: int = 7,
        page: int = 1,
        limit: int = 20,
    ) -> PaginatedResponse[JobListItem]:
        """Get paginated job listing with filters."""
        jobs, total = await self.job_repo.find_with_filters(
            db,
            company_slugs=company_slugs,
            location=location,
            role=role,
            location_type=location_type,
            days_ago=days_ago,
            page=page,
            limit=limit,
        )

        items = [self._to_list_item(job) for job in jobs]

        return PaginatedResponse(
            items=items,
            total=total,
            page=page,
            limit=limit,
            pages=(total + limit - 1) // limit if total > 0 else 0,
        )

    async def get_job_detail(
        self,
        db: AsyncSession,
        job_id: UUID,
    ) -> JobDetail:
        """
        Get full job details including skills.

        Raises:
            JobNotFoundException: If job doesn't exist.
        """
        job = await self.job_repo.get_with_details(db, job_id)

        if not job:
            raise JobNotFoundException()

        return JobDetail(
            id=job.id,
            title=job.title,
            company=CompanyBrief(
                id=job.company.id,
                name=job.company.name,
                slug=job.company.slug,
                logo_url=job.company.logo_url,
            ),
            location=job.location,
            location_type=job.location_type,
            job_type=job.job_type,
            apply_url=job.apply_url,
            posted_at=job.posted_at,
            discovered_at=job.discovered_at,
            is_active=job.is_active,
            description=job.description,
            seniority_level=job.seniority_level,
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            salary_currency=job.salary_currency,
            expires_at=job.expires_at,
            created_at=job.created_at,
            updated_at=job.updated_at,
            skills=[
                JobSkillResponse(
                    skill_name=skill.skill_name,
                    skill_category=skill.skill_category,
                    is_required=skill.is_required,
                    min_years_experience=skill.min_years_experience,
                )
                for skill in job.skills
            ],
        )

    async def analyze_skill_gap(
        self,
        db: AsyncSession,
        job_id: UUID,
        user: User,
    ) -> SkillGapResponse:
        """
        Compare user's skills against a job's requirements.

        Raises:
            JobNotFoundException: If job doesn't exist.
        """
        job = await self.job_repo.get_with_details(db, job_id)
        if not job:
            raise JobNotFoundException()

        user_skills = await self.user_repo.get_user_skills(db, user.id)

        # Build lookup map (lowercase for case-insensitive matching)
        user_skill_map = {s.skill_name.lower(): s for s in user_skills}

        matching = []
        missing = []
        partial = []

        for job_skill in job.skills:
            skill_lower = job_skill.skill_name.lower()

            if skill_lower in user_skill_map:
                user_skill = user_skill_map[skill_lower]

                # Check if experience meets requirement
                if (
                    job_skill.min_years_experience
                    and user_skill.years_experience
                    and float(user_skill.years_experience) < job_skill.min_years_experience
                ):
                    partial.append(PartialSkill(
                        skill_name=job_skill.skill_name,
                        user_years=float(user_skill.years_experience),
                        required_years=job_skill.min_years_experience,
                    ))
                else:
                    matching.append(SkillMatch(
                        skill_name=job_skill.skill_name,
                        user_level=user_skill.proficiency_level,
                    ))
            else:
                missing.append(MissingSkill(
                    skill_name=job_skill.skill_name,
                    is_required=job_skill.is_required,
                    category=job_skill.skill_category,
                ))

        # Calculate match percentage (required skills only)
        required_skills = [s for s in job.skills if s.is_required]
        if required_skills:
            matched_required = len([
                m for m in matching
                if any(
                    s.skill_name.lower() == m.skill_name.lower() and s.is_required
                    for s in job.skills
                )
            ])
            match_percentage = (matched_required / len(required_skills)) * 100
        else:
            match_percentage = 100.0

        # Generate recommendation
        if match_percentage >= 80:
            recommendation = "Strong match! Apply with confidence."
        elif match_percentage >= 60:
            recommendation = "Good match. Emphasize your relevant experience."
        elif match_percentage >= 40:
            recommendation = "Moderate match. Worth applying if interested."
        else:
            recommendation = "Skills gap is significant. Consider upskilling first."

        return SkillGapResponse(
            job_id=job.id,
            job_title=job.title,
            matching_skills=matching,
            missing_skills=missing,
            partial_skills=partial,
            match_percentage=round(match_percentage, 1),
            recommendation=recommendation,
        )

    def _to_list_item(self, job) -> JobListItem:
        """Convert a Job model to a JobListItem response."""
        return JobListItem(
            id=job.id,
            title=job.title,
            company=CompanyBrief(
                id=job.company.id,
                name=job.company.name,
                slug=job.company.slug,
                logo_url=job.company.logo_url,
            ),
            location=job.location,
            location_type=job.location_type,
            job_type=job.job_type,
            apply_url=job.apply_url,
            posted_at=job.posted_at,
            discovered_at=job.discovered_at,
            is_active=job.is_active,
        )
