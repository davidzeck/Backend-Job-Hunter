"""
Admin routes — dashboard statistics and operational endpoints.
"""
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.api.deps import get_admin_user
from app.models.user import User
from app.models.job import Job
from app.models.job_source import JobSource
from app.models.scrape_log import ScrapeLog
from app.models.user_job_alert import UserJobAlert

router = APIRouter(prefix="/dashboard", tags=["admin"])


class DashboardStats(BaseModel):
    total_jobs: int
    new_jobs_today: int
    active_sources: int
    total_sources: int
    scrapes_today: int
    failed_scrapes_today: int
    alerts_sent_today: int


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Aggregate counts for the overview dashboard."""
    today = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    # Total active jobs
    total_jobs_res = await db.execute(
        select(func.count()).select_from(Job).where(Job.is_active == True)
    )
    total_jobs = total_jobs_res.scalar() or 0

    # New jobs discovered today
    new_jobs_res = await db.execute(
        select(func.count()).select_from(Job).where(
            Job.discovered_at >= today
        )
    )
    new_jobs_today = new_jobs_res.scalar() or 0

    # Active sources
    active_sources_res = await db.execute(
        select(func.count()).select_from(JobSource).where(
            JobSource.is_active == True
        )
    )
    active_sources = active_sources_res.scalar() or 0

    # Total sources
    total_sources_res = await db.execute(
        select(func.count()).select_from(JobSource)
    )
    total_sources = total_sources_res.scalar() or 0

    # Scrapes today
    scrapes_today_res = await db.execute(
        select(func.count()).select_from(ScrapeLog).where(
            ScrapeLog.created_at >= today
        )
    )
    scrapes_today = scrapes_today_res.scalar() or 0

    # Failed scrapes today
    failed_scrapes_res = await db.execute(
        select(func.count()).select_from(ScrapeLog).where(
            ScrapeLog.created_at >= today,
            ScrapeLog.status == "failed",
        )
    )
    failed_scrapes_today = failed_scrapes_res.scalar() or 0

    # Alerts sent today
    alerts_today_res = await db.execute(
        select(func.count()).select_from(UserJobAlert).where(
            UserJobAlert.notified_at >= today
        )
    )
    alerts_sent_today = alerts_today_res.scalar() or 0

    return DashboardStats(
        total_jobs=total_jobs,
        new_jobs_today=new_jobs_today,
        active_sources=active_sources,
        total_sources=total_sources,
        scrapes_today=scrapes_today,
        failed_scrapes_today=failed_scrapes_today,
        alerts_sent_today=alerts_sent_today,
    )


# ── Charts: real aggregations for the overview page ──────────────────────────


class JobsTimelinePoint(BaseModel):
    date: str  # ISO YYYY-MM-DD
    jobs: int  # cumulative active jobs through this day
    new_jobs: int  # jobs discovered on this day


@router.get("/jobs-timeline", response_model=List[JobsTimelinePoint])
async def get_jobs_timeline(
    days: int = Query(7, ge=1, le=90),
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """New jobs per day (+ cumulative total) over the last N days."""
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    # Bucket by UTC calendar day as a string, independent of the DB session
    # timezone (date_trunc on a timestamptz would otherwise use the session tz).
    day_expr = func.to_char(func.timezone("UTC", Job.discovered_at), "YYYY-MM-DD")
    rows = (
        await db.execute(
            select(day_expr.label("day"), func.count().label("n"))
            .where(Job.is_active == True, Job.discovered_at >= start)
            .group_by(day_expr)
        )
    ).all()
    per_day = {r.day: r.n for r in rows}  # keys: 'YYYY-MM-DD'

    # Baseline: active jobs discovered before the window
    base = (
        await db.execute(
            select(func.count())
            .select_from(Job)
            .where(Job.is_active == True, Job.discovered_at < start)
        )
    ).scalar() or 0

    points: List[JobsTimelinePoint] = []
    cumulative = base
    for i in range(days):
        key = (start + timedelta(days=i)).date().isoformat()
        new_jobs = per_day.get(key, 0)
        cumulative += new_jobs
        points.append(
            JobsTimelinePoint(date=key, jobs=cumulative, new_jobs=new_jobs)
        )
    return points


class ScrapeActivityPoint(BaseModel):
    hour: str  # ISO datetime (start of the hour)
    scrapes: int
    success: int
    failed: int


@router.get("/scrape-activity", response_model=List[ScrapeActivityPoint])
async def get_scrape_activity(
    hours: int = Query(24, ge=1, le=168),
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Scrape counts (success/failed) bucketed by hour over the last N hours."""
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=hours - 1)).replace(
        minute=0, second=0, microsecond=0
    )

    # Bucket by UTC hour as a string (timezone-independent — see jobs-timeline).
    hour_expr = func.to_char(func.timezone("UTC", ScrapeLog.created_at), "YYYY-MM-DD HH24")
    failed_sum = func.sum(case((ScrapeLog.status == "failed", 1), else_=0))
    rows = (
        await db.execute(
            select(hour_expr.label("h"), func.count().label("n"), failed_sum.label("f"))
            .where(ScrapeLog.created_at >= start)
            .group_by(hour_expr)
        )
    ).all()
    per_hour = {r.h: (r.n, int(r.f or 0)) for r in rows}  # keys: 'YYYY-MM-DD HH'

    points: List[ScrapeActivityPoint] = []
    for i in range(hours):
        h = start + timedelta(hours=i)
        key = h.strftime("%Y-%m-%d %H")
        scrapes, failed = per_hour.get(key, (0, 0))
        points.append(
            ScrapeActivityPoint(
                hour=h.isoformat(), scrapes=scrapes, success=scrapes - failed, failed=failed
            )
        )
    return points


class SourcePerformanceBuckets(BaseModel):
    active: int
    error: int
    paused: int
    inactive: int


class SourcePerformanceResponse(BaseModel):
    data: SourcePerformanceBuckets
    success_rate: float


@router.get("/source-performance", response_model=SourcePerformanceResponse)
async def get_source_performance(
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Source health buckets + recent scrape success rate."""
    sources = (await db.execute(select(JobSource))).scalars().all()
    active = error = paused = inactive = 0
    for s in sources:
        if not s.is_active:
            inactive += 1
        elif s.health_status == "failing":
            error += 1
        elif s.health_status == "degraded":
            paused += 1
        else:  # healthy (or unknown)
            active += 1

    since = datetime.now(timezone.utc) - timedelta(days=7)
    total = (
        await db.execute(
            select(func.count()).select_from(ScrapeLog).where(ScrapeLog.created_at >= since)
        )
    ).scalar() or 0
    ok = (
        await db.execute(
            select(func.count())
            .select_from(ScrapeLog)
            .where(ScrapeLog.created_at >= since, ScrapeLog.status == "success")
        )
    ).scalar() or 0
    success_rate = round((ok / total) * 100, 1) if total else 0.0

    return SourcePerformanceResponse(
        data=SourcePerformanceBuckets(
            active=active, error=error, paused=paused, inactive=inactive
        ),
        success_rate=success_rate,
    )


class ActivityItem(BaseModel):
    id: str
    type: str  # scrape_completed | scrape_failed
    title: str
    description: str
    timestamp: str
    metadata: Optional[dict] = None


@router.get("/activity", response_model=List[ActivityItem])
async def get_activity(
    limit: int = Query(15, ge=1, le=50),
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Recent scrape activity feed, newest first (from scrape logs)."""
    logs = (
        await db.execute(
            select(ScrapeLog)
            .options(selectinload(ScrapeLog.source).selectinload(JobSource.company))
            .order_by(ScrapeLog.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()

    items: List[ActivityItem] = []
    for log in logs:
        source = log.source
        company = source.company if source else None
        source_name = (company.name if company else None) or (
            source.source_type if source else "source"
        )
        if log.status == "success":
            items.append(
                ActivityItem(
                    id=str(log.id),
                    type="scrape_completed",
                    title=f"Scraped {source_name}",
                    description=f"{log.new_jobs} new job(s), {log.jobs_found} found",
                    timestamp=log.created_at.isoformat(),
                    metadata={"sourceName": source_name, "jobCount": log.new_jobs},
                )
            )
        else:
            items.append(
                ActivityItem(
                    id=str(log.id),
                    type="scrape_failed",
                    title=f"Scrape failed: {source_name}",
                    description=(log.error_message or "Unknown error")[:140],
                    timestamp=log.created_at.isoformat(),
                    metadata={"sourceName": source_name, "errorMessage": log.error_message},
                )
            )
    return items
