"""
Job routes.

Thin controllers - all query building, filtering, and skill analysis
lives in JobService. Routes just extract params and delegate.
"""
from typing import Optional, List
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.services.job_service import JobService
from app.schemas.job import JobListItem, JobDetail, SkillGapResponse
from app.schemas.base import PaginatedResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])

job_service = JobService()


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
    """List jobs with optional filters and pagination."""
    return await job_service.list_jobs(
        db,
        company_slugs=company,
        location=location,
        role=role,
        location_type=location_type,
        days_ago=days_ago,
        page=page,
        limit=limit,
    )


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get full job details by ID."""
    return await job_service.get_job_detail(db, job_id)


@router.get("/{job_id}/skill-gap", response_model=SkillGapResponse)
async def get_skill_gap(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Compare user's skills against job requirements."""
    return await job_service.analyze_skill_gap(db, job_id, current_user)
