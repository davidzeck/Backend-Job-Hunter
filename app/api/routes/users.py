"""
User routes.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.user_cv import UserCV
from app.models.user_skill import UserSkill
from app.schemas.user import (
    UserResponse,
    UserProfileResponse,
    UserUpdate,
    UpdateFCMTokenRequest,
)
from app.schemas.base import MessageResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserProfileResponse)
async def get_current_user_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get current user's profile.
    """
    # Get skills count
    skills_result = await db.execute(
        select(func.count(UserSkill.id)).where(UserSkill.user_id == current_user.id)
    )
    skills_count = skills_result.scalar() or 0

    # Check if user has active CV
    cv_result = await db.execute(
        select(UserCV).where(
            UserCV.user_id == current_user.id,
            UserCV.is_active == True,
        )
    )
    has_cv = cv_result.scalar_one_or_none() is not None

    return UserProfileResponse(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        phone=current_user.phone,
        email_verified=current_user.email_verified,
        is_active=current_user.is_active,
        preferences=current_user.preferences,
        last_seen_at=current_user.last_seen_at,
        created_at=current_user.created_at,
        updated_at=current_user.updated_at,
        skills_count=skills_count,
        has_cv=has_cv,
    )


@router.patch("/me", response_model=UserResponse)
async def update_current_user(
    request: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Update current user's profile.
    """
    update_data = request.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(current_user, field, value)

    await db.commit()
    await db.refresh(current_user)

    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        phone=current_user.phone,
        email_verified=current_user.email_verified,
        is_active=current_user.is_active,
        preferences=current_user.preferences,
        last_seen_at=current_user.last_seen_at,
        created_at=current_user.created_at,
        updated_at=current_user.updated_at,
    )


@router.put("/me/preferences", response_model=UserResponse)
async def update_preferences(
    preferences: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Update user notification preferences.
    """
    # Merge with existing preferences
    current_user.preferences = {**current_user.preferences, **preferences}
    await db.commit()
    await db.refresh(current_user)

    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        phone=current_user.phone,
        email_verified=current_user.email_verified,
        is_active=current_user.is_active,
        preferences=current_user.preferences,
        last_seen_at=current_user.last_seen_at,
        created_at=current_user.created_at,
        updated_at=current_user.updated_at,
    )


@router.put("/me/fcm-token", response_model=MessageResponse)
async def update_fcm_token(
    request: UpdateFCMTokenRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Update user's FCM token for push notifications.
    """
    current_user.fcm_token = request.fcm_token
    await db.commit()

    return MessageResponse(message="FCM token updated successfully")


@router.delete("/me", response_model=MessageResponse)
async def delete_current_user(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Delete current user's account.
    Performs soft delete by setting is_active to False.
    """
    current_user.is_active = False
    current_user.fcm_token = None  # Clear FCM token
    await db.commit()

    return MessageResponse(message="Account deleted successfully")
