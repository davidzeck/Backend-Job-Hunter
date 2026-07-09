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
from app.schemas.job import (
    JobListItem,
    JobDetail,
    SkillGapResponse,
    SaveJobRequest,
    AppliedJobRequest,
    JobInteractionResponse,
)
from app.schemas.base import PaginatedResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])

job_service = JobService()


# "" alias: serve /jobs without the trailing slash. Otherwise FastAPI 307s to an
# absolute backend URL, and browsers drop Authorization on that cross-origin hop
# (breaks the dashboard's same-origin proxy).
@router.get("", include_in_schema=False, response_model=PaginatedResponse[JobListItem])
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
        current_user_id=current_user.id,
        company_slugs=company,
        location=location,
        role=role,
        location_type=location_type,
        days_ago=days_ago,
        page=page,
        limit=limit,
    )


# NOTE: /saved must be declared before /{job_id} — otherwise "saved" is parsed
# as a UUID path param and 422s.
@router.get("/saved", response_model=PaginatedResponse[JobListItem])
async def list_saved_jobs(
    page: int = Query(1, ge=1),
    limit: int = Query(20, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the jobs the current user has saved."""
    return await job_service.list_saved_jobs(
        db, current_user.id, page=page, limit=limit
    )


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get full job details by ID."""
    return await job_service.get_job_detail(db, job_id, current_user.id)


@router.put("/{job_id}/saved", response_model=JobInteractionResponse)
async def set_job_saved(
    job_id: UUID,
    body: SaveJobRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save or unsave a job for the current user."""
    return await job_service.set_saved(db, current_user.id, job_id, body.saved)


@router.put("/{job_id}/applied", response_model=JobInteractionResponse)
async def set_job_applied(
    job_id: UUID,
    body: AppliedJobRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark a job applied/not-applied for the current user."""
    return await job_service.set_applied(db, current_user.id, job_id, body.applied)


@router.get("/{job_id}/skill-gap", response_model=SkillGapResponse)
async def get_skill_gap(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Compare user's skills against job requirements."""
    return await job_service.analyze_skill_gap(db, job_id, current_user)
