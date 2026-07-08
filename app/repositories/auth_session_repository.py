"""
Repository for auth_sessions (rotating refresh tokens).
"""
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth_session import AuthSession
from app.repositories.base import BaseRepository


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AuthSessionRepository(BaseRepository[AuthSession]):
    """Data access for refresh-token session rows."""

    def __init__(self):
        super().__init__(AuthSession)

    async def get_family_origin(
        self, db: AsyncSession, family_id: UUID
    ) -> Optional[AuthSession]:
        """The first row of a family (its id equals the family_id)."""
        result = await db.execute(
            select(AuthSession).where(AuthSession.id == family_id)
        )
        return result.scalar_one_or_none()

    async def list_active_tails(
        self, db: AsyncSession, user_id: UUID
    ) -> List[AuthSession]:
        """
        Current (rotatable) token per family: not revoked, not replaced,
        not expired. One row per live login session.
        """
        result = await db.execute(
            select(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.replaced_by.is_(None),
                AuthSession.expires_at > _now(),
            )
            .order_by(AuthSession.created_at.desc())
        )
        return list(result.scalars().all())

    async def revoke_family(self, db: AsyncSession, family_id: UUID) -> int:
        """Revoke every non-revoked row of a family. Returns rows affected."""
        result = await db.execute(
            update(AuthSession)
            .where(
                AuthSession.family_id == family_id,
                AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=_now())
        )
        return result.rowcount or 0

    async def revoke_all_for_user(
        self,
        db: AsyncSession,
        user_id: UUID,
        except_family: Optional[UUID] = None,
    ) -> List[UUID]:
        """
        Revoke all of a user's sessions (optionally sparing one family).
        Returns the distinct family ids revoked (for Redis markers).
        """
        conditions = [
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        ]
        if except_family is not None:
            conditions.append(AuthSession.family_id != except_family)

        families_result = await db.execute(
            select(AuthSession.family_id).where(*conditions).distinct()
        )
        family_ids = [row[0] for row in families_result.all()]

        if family_ids:
            await db.execute(
                update(AuthSession).where(*conditions).values(revoked_at=_now())
            )
        return family_ids

    async def mark_replaced(
        self, db: AsyncSession, old_row: AuthSession, new_id: UUID
    ) -> None:
        """Chain a rotation: old token replaced by the new row."""
        old_row.replaced_by = new_id
        old_row.last_used_at = _now()

    async def user_owns_family(
        self, db: AsyncSession, user_id: UUID, family_id: UUID
    ) -> bool:
        result = await db.execute(
            select(AuthSession.id)
            .where(
                AuthSession.family_id == family_id,
                AuthSession.user_id == user_id,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None
