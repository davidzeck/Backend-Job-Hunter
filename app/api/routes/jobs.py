"""
Job routes.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.exceptions import JobNotFoundException
from app.api.deps import get_current_user
from app.models.user import User
from app.models.job import Job
from app.models.company import Company
from app.models.job_skill import JobSkill
from app.models.user_skill import UserSkill
from app.schemas.job import (
    JobListItem,
    JobDetail,
    SkillGapResponse,
    SkillMatch,
    MissingSkill,
    PartialSkill,
    CompanyBrief,
    JobSkillResponse,
)
from app.schemas.base import PaginatedResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/", response_model=PaginatedResponse[JobListItem])
async def list_jobs(
    company: Optional[List[str]] = Query(None, description="Company slugs"),
    location: Optional[str] = Query(None, description="Location filter"),
    role: Optional[str] = Query(None, description="Role keyword search"),
    location_type: Optional[str] = Query(None, description="remote, onsite, hybrid"),
    days_ago: int = Query(7, le=30, description="Jobs from last N days"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    List jobs with optional filters.
    """
    # Base query
    query = (
        select(Job)
        .options(selectinload(Job.company))
        .where(Job.is_active == True)
    )

    # Apply filters
    filters = []

    if company:
        filters.append(Job.company.has(Company.slug.in_(company)))

    if location:
        filters.append(Job.location.ilike(f"%{location}%"))

    if role:
        filters.append(Job.title.ilike(f"%{role}%"))

    if location_type:
        filters.append(Job.location_type == location_type)

    # Time filter
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_ago)
    filters.append(Job.discovered_at >= cutoff)

    if filters:
        query = query.where(and_(*filters))

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination
    query = query.order_by(Job.discovered_at.desc())
    query = query.offset((page - 1) * limit).limit(limit)

    result = await db.execute(query)
    jobs = result.scalars().all()

    # Transform to response
    items = [
        JobListItem(
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
        for job in jobs
    ]

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        limit=limit,
        pages=(total + limit - 1) // limit,
    )


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get job details by ID.
    """
    result = await db.execute(
        select(Job)
        .options(selectinload(Job.company), selectinload(Job.skills))
        .where(Job.id == job_id)
    )
    job = result.scalar_one_or_none()

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


@router.get("/{job_id}/skill-gap", response_model=SkillGapResponse)
async def get_skill_gap(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Compare user's skills against job requirements.
    """
    # Get job with skills
    job_result = await db.execute(
        select(Job)
        .options(selectinload(Job.skills))
        .where(Job.id == job_id)
    )
    job = job_result.scalar_one_or_none()

    if not job:
        raise JobNotFoundException()

    # Get user skills
    skills_result = await db.execute(
        select(UserSkill).where(UserSkill.user_id == current_user.id)
    )
    user_skills = skills_result.scalars().all()

    # Create skill map (lowercase for matching)
    user_skill_map = {s.skill_name.lower(): s for s in user_skills}

    # Analyze gaps
    matching = []
    missing = []
    partial = []

    for job_skill in job.skills:
        skill_lower = job_skill.skill_name.lower()

        if skill_lower in user_skill_map:
            user_skill = user_skill_map[skill_lower]

            # Check experience requirement
            if (
                job_skill.min_years_experience
                and user_skill.years_experience
                and float(user_skill.years_experience) < job_skill.min_years_experience
            ):
                partial.append(
                    PartialSkill(
                        skill_name=job_skill.skill_name,
                        user_years=float(user_skill.years_experience) if user_skill.years_experience else None,
                        required_years=job_skill.min_years_experience,
                    )
                )
            else:
                matching.append(
                    SkillMatch(
                        skill_name=job_skill.skill_name,
                        user_level=user_skill.proficiency_level,
                    )
                )
        else:
            missing.append(
                MissingSkill(
                    skill_name=job_skill.skill_name,
                    is_required=job_skill.is_required,
                    category=job_skill.skill_category,
                )
            )

    # Calculate match percentage (required skills only)
    required_skills = [s for s in job.skills if s.is_required]
    if required_skills:
        matched_required = len(
            [m for m in matching if any(
                s.skill_name.lower() == m.skill_name.lower() and s.is_required
                for s in job.skills
            )]
        )
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
