"""
User repository - data access for User entity.
"""
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.user import User
from app.models.user_skill import UserSkill
from app.models.user_cv import UserCV
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    def __init__(self):
        super().__init__(User)

    async def get_by_email(
        self,
        db: AsyncSession,
        email: str,
    ) -> Optional[User]:
        """Find a user by email address."""
        result = await db.execute(
            select(User).where(User.email == email)
        )
        return result.scalar_one_or_none()

    async def get_active_by_email(
        self,
        db: AsyncSession,
        email: str,
    ) -> Optional[User]:
        """Find an active user by email."""
        result = await db.execute(
            select(User).where(
                User.email == email,
                User.is_active == True,
            )
        )
        return result.scalar_one_or_none()

    async def get_active_by_id(
        self,
        db: AsyncSession,
        user_id: UUID,
    ) -> Optional[User]:
        """Find an active user by ID."""
        result = await db.execute(
            select(User).where(
                User.id == user_id,
                User.is_active == True,
            )
        )
        return result.scalar_one_or_none()

    async def get_with_skills(
        self,
        db: AsyncSession,
        user_id: UUID,
    ) -> Optional[User]:
        """Get user with their skills eagerly loaded."""
        result = await db.execute(
            select(User)
            .options(selectinload(User.skills))
            .where(User.id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_notifiable_users(
        self,
        db: AsyncSession,
    ) -> List[User]:
        """Get all active users with FCM tokens (eligible for push)."""
        result = await db.execute(
            select(User).where(
                User.is_active == True,
                User.fcm_token.isnot(None),
            )
        )
        return list(result.scalars().all())

    async def get_user_skills(
        self,
        db: AsyncSession,
        user_id: UUID,
    ) -> List[UserSkill]:
        """Get all skills for a user."""
        result = await db.execute(
            select(UserSkill).where(UserSkill.user_id == user_id)
        )
        return list(result.scalars().all())

    async def count_skills(
        self,
        db: AsyncSession,
        user_id: UUID,
    ) -> int:
        """Count skills for a user."""
        result = await db.execute(
            select(func.count(UserSkill.id)).where(UserSkill.user_id == user_id)
        )
        return result.scalar() or 0

    async def has_active_cv(
        self,
        db: AsyncSession,
        user_id: UUID,
    ) -> bool:
        """Check if a user has an active CV uploaded."""
        result = await db.execute(
            select(UserCV.id).where(
                UserCV.user_id == user_id,
                UserCV.is_active == True,
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def email_exists(
        self,
        db: AsyncSession,
        email: str,
    ) -> bool:
        """Check if an email is already registered."""
        result = await db.execute(
            select(User.id).where(User.email == email)
        )
        return result.scalar_one_or_none() is not None
