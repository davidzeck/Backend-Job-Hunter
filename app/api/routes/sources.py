"""
Sources routes — CRUD for job sources and scrape trigger.

JobSource.url maps to source_url in the API response.
JobSource.health_status maps to scraper_status in the API response.
"""
from datetime import datetime
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.job_source import JobSource
from app.models.job import Job
from app.models.scrape_log import ScrapeLog
from app.schemas.base import PaginatedResponse, MessageResponse
from app.schemas.company import CompanyBase

router = APIRouter(prefix="/sources", tags=["sources"])


# ─── Schemas ────────────────────────────────────────────────────


class CompanyBriefForSource(BaseModel):
    id: UUID
    name: str
    slug: str
    logo_url: Optional[str] = None

    model_config = {"from_attributes": True}


class JobSourceResponse(BaseModel):
    id: UUID
    company_id: UUID
    company: Optional[CompanyBriefForSource] = None
    source_type: str
    source_url: str          # mapped from JobSource.url
    is_active: bool
    scraper_status: str      # mapped from JobSource.health_status
    scraper_class: str
    scrape_interval_minutes: int
    last_scraped_at: Optional[datetime] = None
    last_error: Optional[str] = None
    jobs_found_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class JobSourceCreate(BaseModel):
    company_id: UUID
    source_type: str
    source_url: str
    scraper_class: str
    scrape_interval_minutes: int = 60
    is_active: bool = True


class JobSourceUpdate(BaseModel):
    source_url: Optional[str] = None
    is_active: Optional[bool] = None
    scrape_interval_minutes: Optional[int] = None
    scraper_class: Optional[str] = None


class ScrapeLogResponse(BaseModel):
    id: UUID
    source_id: UUID
    status: str
    jobs_found: int
    new_jobs: int
    updated_jobs: int
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Helpers ────────────────────────────────────────────────────


async def _build_response(
    source: JobSource,
    db: AsyncSession,
) -> JobSourceResponse:
    """Build a JobSourceResponse from a JobSource ORM instance."""
    # Count active jobs for this source
    jobs_count_res = await db.execute(
        select(func.count()).select_from(Job).where(
            Job.source_id == source.id,
            Job.is_active == True,
        )
    )
    jobs_found_count = jobs_count_res.scalar() or 0

    # Get most recent error message from scrape logs
    last_error: Optional[str] = None
    latest_failed = await db.execute(
        select(ScrapeLog)
        .where(ScrapeLog.source_id == source.id, ScrapeLog.status == "failed")
        .order_by(desc(ScrapeLog.created_at))
        .limit(1)
    )
    failed_log = latest_failed.scalar_one_or_none()
    if failed_log:
        last_error = failed_log.error_message

    # Load company if not already loaded
    company = None
    if hasattr(source, "company") and source.company is not None:
        c = source.company
        company = CompanyBriefForSource(
            id=c.id,
            name=c.name,
            slug=c.slug,
            logo_url=c.logo_url,
        )

    return JobSourceResponse(
        id=source.id,
        company_id=source.company_id,
        company=company,
        source_type=source.source_type,
        source_url=source.url,
        is_active=source.is_active,
        scraper_status=source.health_status,
        scraper_class=source.scraper_class,
        scrape_interval_minutes=source.scrape_interval_minutes,
        last_scraped_at=source.last_scraped_at,
        last_error=last_error,
        jobs_found_count=jobs_found_count,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


# ─── Routes ─────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedResponse[JobSourceResponse])
async def list_sources(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    company_id: Optional[UUID] = None,
    is_active: Optional[bool] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all job sources with optional filters."""
    query = select(JobSource).options(selectinload(JobSource.company))

    if company_id is not None:
        query = query.where(JobSource.company_id == company_id)
    if is_active is not None:
        query = query.where(JobSource.is_active == is_active)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_res = await db.execute(count_query)
    total = total_res.scalar() or 0

    # Paginate
    skip = (page - 1) * limit
    query = query.order_by(desc(JobSource.created_at)).offset(skip).limit(limit)
    result = await db.execute(query)
    sources = list(result.scalars().all())

    items = [await _build_response(s, db) for s in sources]
    pages = (total + limit - 1) // limit if total > 0 else 1

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        limit=limit,
        pages=pages,
    )


@router.post("/", response_model=JobSourceResponse, status_code=201)
async def create_source(
    data: JobSourceCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new job source."""
    source = JobSource(
        company_id=data.company_id,
        source_type=data.source_type,
        url=data.source_url,
        scraper_class=data.scraper_class,
        scrape_interval_minutes=data.scrape_interval_minutes,
        is_active=data.is_active,
        health_status="unknown",
        config={},
    )
    db.add(source)
    await db.flush()
    await db.refresh(source)

    # Load company for response
    await db.execute(
        select(JobSource)
        .options(selectinload(JobSource.company))
        .where(JobSource.id == source.id)
    )
    await db.commit()
    return await _build_response(source, db)


@router.get("/{source_id}", response_model=JobSourceResponse)
async def get_source(
    source_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single job source by ID."""
    result = await db.execute(
        select(JobSource)
        .options(selectinload(JobSource.company))
        .where(JobSource.id == source_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return await _build_response(source, db)


@router.patch("/{source_id}", response_model=JobSourceResponse)
async def update_source(
    source_id: UUID,
    data: JobSourceUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a job source's URL, active status, or schedule."""
    result = await db.execute(
        select(JobSource)
        .options(selectinload(JobSource.company))
        .where(JobSource.id == source_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    if data.source_url is not None:
        source.url = data.source_url
    if data.is_active is not None:
        source.is_active = data.is_active
        # Reset health when re-enabling
        if data.is_active:
            source.health_status = "unknown"
            source.consecutive_failures = 0
    if data.scrape_interval_minutes is not None:
        source.scrape_interval_minutes = data.scrape_interval_minutes
    if data.scraper_class is not None:
        source.scraper_class = data.scraper_class

    await db.flush()
    await db.refresh(source)
    await db.commit()
    return await _build_response(source, db)


@router.delete("/{source_id}", response_model=MessageResponse)
async def delete_source(
    source_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a job source."""
    result = await db.execute(
        select(JobSource).where(JobSource.id == source_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    await db.delete(source)
    await db.commit()
    return MessageResponse(message="Source deleted successfully")


@router.post("/{source_id}/scrape", response_model=dict)
async def trigger_scrape(
    source_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger a scrape for a specific source."""
    result = await db.execute(
        select(JobSource).where(JobSource.id == source_id, JobSource.is_active == True)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Active source not found")

    from app.workers.tasks import scrape_source
    task = scrape_source.delay(str(source_id))

    return {"message": "Scrape triggered", "task_id": task.id}


@router.get("/{source_id}/logs", response_model=PaginatedResponse[ScrapeLogResponse])
async def get_scrape_logs(
    source_id: UUID,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get paginated scrape logs for a source."""
    # Verify source exists
    source_res = await db.execute(
        select(JobSource).where(JobSource.id == source_id)
    )
    if not source_res.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Source not found")

    # Count
    count_res = await db.execute(
        select(func.count()).select_from(ScrapeLog).where(
            ScrapeLog.source_id == source_id
        )
    )
    total = count_res.scalar() or 0

    # Paginate
    skip = (page - 1) * limit
    logs_res = await db.execute(
        select(ScrapeLog)
        .where(ScrapeLog.source_id == source_id)
        .order_by(desc(ScrapeLog.created_at))
        .offset(skip)
        .limit(limit)
    )
    logs = list(logs_res.scalars().all())
    pages = (total + limit - 1) // limit if total > 0 else 1

    return PaginatedResponse(
        items=logs,
        total=total,
        page=page,
        limit=limit,
        pages=pages,
    )
