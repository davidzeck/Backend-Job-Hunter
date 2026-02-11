"""
Company repository - data access for Company entity.
"""
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.company import Company
from app.models.job import Job
from app.models.job_source import JobSource
from app.repositories.base import BaseRepository


class CompanyRepository(BaseRepository[Company]):
    def __init__(self):
        super().__init__(Company)

    async def get_by_slug(
        self,
        db: AsyncSession,
        slug: str,
    ) -> Optional[Company]:
        """Find a company by its URL slug."""
        result = await db.execute(
            select(Company).where(Company.slug == slug)
        )
        return result.scalar_one_or_none()

    async def get_with_counts(
        self,
        db: AsyncSession,
        *,
        active_only: bool = True,
        skip: int = 0,
        limit: int = 50,
    ) -> List[dict]:
        """
        Get companies with their job and source counts.

        Returns list of dicts with company + counts.
        """
        query = select(Company)

        if active_only:
            query = query.where(Company.is_active == True)

        query = query.order_by(Company.name).offset(skip).limit(limit)
        result = await db.execute(query)
        companies = list(result.scalars().all())

        enriched = []
        for company in companies:
            # Count active jobs
            job_count_result = await db.execute(
                select(func.count()).where(
                    Job.company_id == company.id,
                    Job.is_active == True,
                )
            )
            # Count sources
            source_count_result = await db.execute(
                select(func.count()).where(
                    JobSource.company_id == company.id,
                    JobSource.is_active == True,
                )
            )
            enriched.append({
                "company": company,
                "active_jobs": job_count_result.scalar() or 0,
                "active_sources": source_count_result.scalar() or 0,
            })

        return enriched

    async def slug_exists(
        self,
        db: AsyncSession,
        slug: str,
    ) -> bool:
        """Check if a company slug already exists."""
        result = await db.execute(
            select(Company.id).where(Company.slug == slug)
        )
        return result.scalar_one_or_none() is not None
