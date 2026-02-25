"""
Admin routes — dashboard statistics and operational endpoints.
"""
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.deps import get_current_user
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
    current_user: User = Depends(get_current_user),
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
