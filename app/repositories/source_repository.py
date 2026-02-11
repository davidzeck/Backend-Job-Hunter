"""
Source repository - data access for JobSource entity.
"""
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job_source import JobSource
from app.repositories.base import BaseRepository


class SourceRepository(BaseRepository[JobSource]):
    def __init__(self):
        super().__init__(JobSource)

    async def get_active_sources(
        self,
        db: AsyncSession,
    ) -> List[JobSource]:
        """Get all active job sources."""
        result = await db.execute(
            select(JobSource).where(JobSource.is_active == True)
        )
        return list(result.scalars().all())

    async def get_due_sources(
        self,
        db: AsyncSession,
    ) -> List[JobSource]:
        """Get active sources that are due for scraping."""
        now = datetime.now(timezone.utc)

        result = await db.execute(
            select(JobSource).where(
                JobSource.is_active == True,
                or_(
                    JobSource.last_scraped_at.is_(None),
                    JobSource.last_scraped_at < now - timedelta(minutes=15),
                ),
            )
        )
        sources = list(result.scalars().all())

        # Further filter by each source's own interval
        due = []
        for source in sources:
            if source.last_scraped_at:
                interval = timedelta(minutes=source.scrape_interval_minutes)
                if now - source.last_scraped_at < interval:
                    continue
            due.append(source)

        return due

    async def get_failing_sources(
        self,
        db: AsyncSession,
    ) -> List[JobSource]:
        """Get sources that are currently failing."""
        result = await db.execute(
            select(JobSource).where(JobSource.health_status == "failing")
        )
        return list(result.scalars().all())

    async def get_by_company(
        self,
        db: AsyncSession,
        company_id: UUID,
    ) -> List[JobSource]:
        """Get all sources for a company."""
        result = await db.execute(
            select(JobSource).where(
                JobSource.company_id == company_id,
                JobSource.is_active == True,
            )
        )
        return list(result.scalars().all())
