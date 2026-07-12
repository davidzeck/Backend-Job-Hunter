"""
Job repository - data access for Job entity.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select, func, and_, case
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
        validation_status: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
    ) -> Tuple[List[Job], int]:
        """
        Find jobs with filters, pagination, and total count.

        `validation_status` (admin) filters to that exact status; when omitted,
        `dead` jobs are excluded from the default feed.

        Returns:
            Tuple of (jobs list, total count)
        """
        query = (
            select(Job)
            .options(selectinload(Job.company))
            .where(Job.is_active == True)
        )

        filters = []

        if validation_status:
            filters.append(Job.validation_status == validation_status)
        else:
            filters.append(Job.validation_status != "dead")

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

    async def find_recommended(
        self,
        db: AsyncSession,
        *,
        user_skill_names: List[str],
        days_ago: int = 30,
        page: int = 1,
        limit: int = 20,
    ) -> Tuple[List[Tuple[UUID, float, List[str]]], int]:
        """
        Rank active, non-dead jobs by weighted skill-coverage against the user's
        skills. One grouped query; pagination + scoring in SQL.

        Returns ((job_id, score_0_1, matched_skill_names)[], total).
        Only jobs with ≥1 matched skill are included (HAVING). Empty input →
        empty result (a user with no skills gets nothing to rank).
        """
        if not user_skill_names:
            return [], 0

        lowered = [s.lower() for s in user_skill_names]
        matched = func.lower(JobSkill.skill_name).in_(lowered)
        weight = case((JobSkill.is_required == True, 2), else_=1)  # noqa: E712

        matched_weight = func.sum(case((matched, weight), else_=0))
        total_weight = func.sum(weight)
        score = (matched_weight / func.nullif(total_weight, 0)).label("score")
        matched_names = func.array_agg(
            case((matched, JobSkill.skill_name), else_=None)
        ).label("matched_names")

        cutoff = datetime.now(timezone.utc) - timedelta(days=days_ago)
        base = (
            select(Job.id, score, matched_names, Job.discovered_at)
            .join(JobSkill, JobSkill.job_id == Job.id)
            .where(
                Job.is_active == True,  # noqa: E712
                Job.validation_status != "dead",
                Job.discovered_at >= cutoff,
            )
            .group_by(Job.id)
            .having(matched_weight > 0)
        )

        # Total distinct matching jobs (for pagination).
        count_result = await db.execute(
            select(func.count()).select_from(base.subquery())
        )
        total = count_result.scalar() or 0

        ranked = (
            base.order_by(score.desc(), Job.discovered_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        result = await db.execute(ranked)

        rows: List[Tuple[UUID, float, List[str]]] = []
        for job_id, score_val, names, _discovered in result.all():
            clean = [n for n in (names or []) if n is not None]
            rows.append((job_id, float(score_val or 0.0), clean))
        return rows, total

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

    async def get_many_with_company(
        self,
        db: AsyncSession,
        job_ids: List[UUID],
    ) -> Dict[UUID, Job]:
        """Load jobs (company eager-loaded) by id → {id: Job}. Order-independent;
        callers preserve their own ordering."""
        if not job_ids:
            return {}
        result = await db.execute(
            select(Job)
            .options(selectinload(Job.company))
            .where(Job.id.in_(job_ids))
        )
        return {job.id: job for job in result.scalars().all()}

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

    async def find_cross_source_duplicate(
        self,
        db: AsyncSession,
        *,
        company_id: UUID,
        source_id: UUID,
        title: str,
        exclude_job_id: UUID,
        within_days: int = 30,
    ) -> Optional[Job]:
        """
        Find an active job from a DIFFERENT source for the same company whose
        normalized title matches — a cross-source duplicate. (Same-source dups
        are already caught by the (source_id, external_id) constraint.)

        Title normalization is Python-side (strips seniority/punctuation), so we
        fetch the small candidate set and compare in memory.
        """
        from app.services.validation_service import normalize_title

        cutoff = datetime.now(timezone.utc) - timedelta(days=within_days)
        result = await db.execute(
            select(Job).where(
                Job.company_id == company_id,
                Job.source_id != source_id,
                Job.id != exclude_job_id,
                Job.is_active == True,
                Job.discovered_at >= cutoff,
            )
        )
        target = normalize_title(title)
        for candidate in result.scalars():
            if normalize_title(candidate.title) == target:
                return candidate
        return None

    async def get_stale_active_jobs(
        self,
        db: AsyncSession,
        *,
        older_than_days: int,
        limit: int,
    ) -> List[Job]:
        """Active, non-dead jobs whose last validation is oldest — for the nightly
        staleness sweep. Never-validated rows (last_validated_at IS NULL) sort first."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        result = await db.execute(
            select(Job)
            .where(
                Job.is_active == True,
                Job.validation_status != "dead",
                Job.discovered_at < cutoff,
            )
            .order_by(Job.last_validated_at.asc().nullsfirst())
            .limit(limit)
        )
        return list(result.scalars().all())

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
