"""
JobInteraction repository - data access for UserJobInteraction (browse Save/Applied).
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.job import Job
from app.models.user_job_interaction import UserJobInteraction
from app.repositories.base import BaseRepository


class JobInteractionRepository(BaseRepository[UserJobInteraction]):
    def __init__(self):
        super().__init__(UserJobInteraction)

    async def _upsert(
        self,
        db: AsyncSession,
        user_id: UUID,
        job_id: UUID,
        values: dict,
    ) -> UserJobInteraction:
        """Insert-or-update the (user, job) interaction row with the given values."""
        stmt = (
            pg_insert(UserJobInteraction)
            .values(user_id=user_id, job_id=job_id, **values)
            .on_conflict_do_update(
                constraint="uq_user_job_interaction",
                set_=values,
            )
            .returning(UserJobInteraction)
        )
        result = await db.execute(stmt)
        await db.flush()
        return result.scalar_one()

    async def set_saved(
        self, db: AsyncSession, user_id: UUID, job_id: UUID, saved: bool
    ) -> UserJobInteraction:
        now = datetime.now(timezone.utc)
        return await self._upsert(
            db, user_id, job_id,
            {"saved": saved, "saved_at": now if saved else None},
        )

    async def set_applied(
        self, db: AsyncSession, user_id: UUID, job_id: UUID, applied: bool
    ) -> UserJobInteraction:
        now = datetime.now(timezone.utc)
        return await self._upsert(
            db, user_id, job_id,
            {"applied": applied, "applied_at": now if applied else None},
        )

    async def get(
        self, db: AsyncSession, user_id: UUID, job_id: UUID
    ) -> Optional[UserJobInteraction]:
        result = await db.execute(
            select(UserJobInteraction).where(
                UserJobInteraction.user_id == user_id,
                UserJobInteraction.job_id == job_id,
            )
        )
        return result.scalar_one_or_none()

    async def map_for_jobs(
        self, db: AsyncSession, user_id: UUID, job_ids: List[UUID]
    ) -> Dict[UUID, Tuple[bool, bool]]:
        """Return {job_id: (saved, applied)} for the user's interactions — single query."""
        if not job_ids:
            return {}
        result = await db.execute(
            select(
                UserJobInteraction.job_id,
                UserJobInteraction.saved,
                UserJobInteraction.applied,
            ).where(
                UserJobInteraction.user_id == user_id,
                UserJobInteraction.job_id.in_(job_ids),
            )
        )
        return {row.job_id: (row.saved, row.applied) for row in result.all()}

    async def list_saved(
        self, db: AsyncSession, user_id: UUID, *, page: int = 1, limit: int = 20
    ) -> Tuple[List[Job], int]:
        """Paginated list of active jobs the user has saved (most recently saved first)."""
        base = (
            select(Job)
            .join(
                UserJobInteraction,
                UserJobInteraction.job_id == Job.id,
            )
            .where(
                UserJobInteraction.user_id == user_id,
                UserJobInteraction.saved == True,
                Job.is_active == True,
            )
        )

        count_query = select(func.count()).select_from(base.subquery())
        total = (await db.execute(count_query)).scalar() or 0

        query = (
            base.options(selectinload(Job.company))
            .order_by(UserJobInteraction.saved_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        result = await db.execute(query)
        return list(result.scalars().all()), total
