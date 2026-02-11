"""
User service - business logic for user profile management.

This service owns ALL user profile operations. Routes never touch
the database directly - they call methods here.
"""
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.core.exceptions import BadRequestException
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.user import UserResponse, UserProfileResponse


class UserService:
    """Handles user profile operations."""

    def __init__(self):
        self.user_repo = UserRepository()

    async def get_profile(
        self,
        db: AsyncSession,
        user: User,
    ) -> UserProfileResponse:
        """
        Get the full user profile with aggregated counts.

        Why this lives in the service and not the route:
        - Aggregating data from multiple sources (user + skills + CVs) is business logic
        - The route shouldn't know what counts to fetch or how to fetch them
        """
        skills_count = await self.user_repo.count_skills(db, user.id)
        has_cv = await self.user_repo.has_active_cv(db, user.id)

        return UserProfileResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            phone=user.phone,
            email_verified=user.email_verified,
            is_active=user.is_active,
            preferences=user.preferences,
            last_seen_at=user.last_seen_at,
            created_at=user.created_at,
            updated_at=user.updated_at,
            skills_count=skills_count,
            has_cv=has_cv,
        )

    async def update_profile(
        self,
        db: AsyncSession,
        user: User,
        *,
        full_name: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> UserResponse:
        """Update user profile fields."""
        updates = {}
        if full_name is not None:
            updates["full_name"] = full_name
        if phone is not None:
            updates["phone"] = phone

        if updates:
            user = await self.user_repo.update(db, user, **updates)
            await db.commit()

        return self._to_response(user)

    async def update_preferences(
        self,
        db: AsyncSession,
        user: User,
        preferences: dict,
    ) -> UserResponse:
        """
        Update user notification/filter preferences.

        Why merge instead of replace? Users may update one section
        (e.g., notifications) without sending the full preferences object.
        Merging prevents accidentally clearing their other settings.
        """
        current = user.preferences or {}
        current.update(preferences)

        user = await self.user_repo.update(db, user, preferences=current)
        await db.commit()

        return self._to_response(user)

    async def update_fcm_token(
        self,
        db: AsyncSession,
        user: User,
        fcm_token: str,
    ) -> None:
        """Update user's Firebase Cloud Messaging token."""
        await self.user_repo.update(db, user, fcm_token=fcm_token)
        await db.commit()

    async def change_password(
        self,
        db: AsyncSession,
        user: User,
        *,
        current_password: str,
        new_password: str,
    ) -> None:
        """
        Change user's password.

        Raises:
            BadRequestException: If current password is wrong.
        """
        if not verify_password(current_password, user.password_hash):
            raise BadRequestException("Current password is incorrect")

        new_hash = hash_password(new_password)
        await self.user_repo.update(db, user, password_hash=new_hash)
        await db.commit()

    async def deactivate_account(
        self,
        db: AsyncSession,
        user: User,
    ) -> None:
        """
        Soft-delete a user account.

        Why soft-delete? We clear the FCM token to stop notifications
        but keep the record for data integrity (their alerts, history, etc.)
        """
        await self.user_repo.update(db, user, is_active=False, fcm_token=None)
        await db.commit()

    def _to_response(self, user: User) -> UserResponse:
        """
        Convert a User model to UserResponse schema.

        Centralized here so every method returns consistent data.
        If the response shape changes, we fix it in one place.
        """
        return UserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            phone=user.phone,
            email_verified=user.email_verified,
            is_active=user.is_active,
            preferences=user.preferences,
            last_seen_at=user.last_seen_at,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )
