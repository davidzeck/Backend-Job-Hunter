"""
Alert repository - data access for UserJobAlert entity.
"""
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.user_job_alert import UserJobAlert
from app.models.job import Job
from app.repositories.base import BaseRepository


class AlertRepository(BaseRepository[UserJobAlert]):
    def __init__(self):
        super().__init__(UserJobAlert)

    async def find_for_user(
        self,
        db: AsyncSession,
        user_id: UUID,
        *,
        is_read: Optional[bool] = None,
        is_saved: Optional[bool] = None,
        page: int = 1,
        limit: int = 20,
    ) -> tuple[List[UserJobAlert], int]:
        """Get alerts for a user with optional filters."""
        query = (
            select(UserJobAlert)
            .options(selectinload(UserJobAlert.job).selectinload(Job.company))
            .where(UserJobAlert.user_id == user_id)
        )

        if is_read is not None:
            query = query.where(UserJobAlert.is_read == is_read)

        if is_saved is not None:
            query = query.where(UserJobAlert.is_saved == is_saved)

        # Count
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await db.execute(count_query)
        total = total_result.scalar() or 0

        # Paginate
        query = query.order_by(UserJobAlert.notified_at.desc())
        query = query.offset((page - 1) * limit).limit(limit)

        result = await db.execute(query)
        alerts = list(result.scalars().all())

        return alerts, total

    async def find_by_user_and_job(
        self,
        db: AsyncSession,
        user_id: UUID,
        job_id: UUID,
    ) -> Optional[UserJobAlert]:
        """Check if user already has an alert for this job."""
        result = await db.execute(
            select(UserJobAlert).where(
                UserJobAlert.user_id == user_id,
                UserJobAlert.job_id == job_id,
            )
        )
        return result.scalar_one_or_none()

    async def count_unread(
        self,
        db: AsyncSession,
        user_id: UUID,
    ) -> int:
        """Count unread alerts for a user."""
        result = await db.execute(
            select(func.count()).where(
                UserJobAlert.user_id == user_id,
                UserJobAlert.is_read == False,
            )
        )
        return result.scalar() or 0

    async def mark_all_read(
        self,
        db: AsyncSession,
        user_id: UUID,
    ) -> int:
        """Mark all alerts as read for a user. Returns count updated."""
        result = await db.execute(
            update(UserJobAlert)
            .where(
                UserJobAlert.user_id == user_id,
                UserJobAlert.is_read == False,
            )
            .values(is_read=True)
        )
        await db.flush()
        return result.rowcount
