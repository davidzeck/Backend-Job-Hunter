"""
Job repository - data access for Job entity.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.job import Job
from app.models.company import Company
from app.models.job_skill import JobSkill
from app.repositories.base import BaseRepository


class JobRepository(BaseRepository[Job]):
    def __init__(self):
        super().__init__(Job)

    async def find_with_filters(
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
    ) -> Tuple[List[Job], int]:
        """
        Find jobs with filters, pagination, and total count.

        Returns:
            Tuple of (jobs list, total count)
        """
        query = (
            select(Job)
            .options(selectinload(Job.company))
            .where(Job.is_active == True)
        )

        filters = []

        if company_slugs:
            filters.append(Job.company.has(Company.slug.in_(company_slugs)))

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

        # Total count
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await db.execute(count_query)
        total = total_result.scalar() or 0

        # Pagination
        query = query.order_by(Job.discovered_at.desc())
        query = query.offset((page - 1) * limit).limit(limit)

        result = await db.execute(query)
        jobs = list(result.scalars().all())

        return jobs, total

    async def get_with_details(
        self,
        db: AsyncSession,
        job_id: UUID,
    ) -> Optional[Job]:
        """Get a job with company and skills eagerly loaded."""
        result = await db.execute(
            select(Job)
            .options(selectinload(Job.company), selectinload(Job.skills))
            .where(Job.id == job_id)
        )
        return result.scalar_one_or_none()

    async def get_with_company(
        self,
        db: AsyncSession,
        job_id: UUID,
    ) -> Optional[Job]:
        """Get a job with company eagerly loaded."""
        result = await db.execute(
            select(Job)
            .options(selectinload(Job.company))
            .where(Job.id == job_id)
        )
        return result.scalar_one_or_none()

    async def find_by_source_and_external_id(
        self,
        db: AsyncSession,
        source_id: UUID,
        external_id: str,
    ) -> Optional[Job]:
        """Find a job by its source and external ID (for deduplication)."""
        result = await db.execute(
            select(Job).where(
                Job.source_id == source_id,
                Job.external_id == external_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_job_skills(
        self,
        db: AsyncSession,
        job_id: UUID,
    ) -> List[JobSkill]:
        """Get all skills required for a job."""
        result = await db.execute(
            select(JobSkill).where(JobSkill.job_id == job_id)
        )
        return list(result.scalars().all())

    async def count_by_company(
        self,
        db: AsyncSession,
        company_id: UUID,
    ) -> int:
        """Count active jobs for a company."""
        result = await db.execute(
            select(func.count()).where(
                Job.company_id == company_id,
                Job.is_active == True,
            )
        )
        return result.scalar() or 0
