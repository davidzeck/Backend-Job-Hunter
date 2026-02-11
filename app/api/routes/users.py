"""
User routes.

Thin controllers - all business logic lives in UserService.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.services.user_service import UserService
from app.schemas.user import (
    UserResponse,
    UserProfileResponse,
    UserUpdate,
    UpdateFCMTokenRequest,
)
from app.schemas.auth import ChangePasswordRequest
from app.schemas.base import MessageResponse

router = APIRouter(prefix="/users", tags=["users"])

user_service = UserService()


@router.get("/me", response_model=UserProfileResponse)
async def get_current_user_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current user's profile with skill/CV counts."""
    return await user_service.get_profile(db, current_user)


@router.patch("/me", response_model=UserResponse)
async def update_current_user(
    request: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update current user's profile."""
    return await user_service.update_profile(
        db,
        current_user,
        full_name=request.full_name,
        phone=request.phone,
    )


@router.put("/me/preferences", response_model=UserResponse)
async def update_preferences(
    preferences: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update user notification preferences."""
    return await user_service.update_preferences(db, current_user, preferences)


@router.put("/me/fcm-token", response_model=MessageResponse)
async def update_fcm_token(
    request: UpdateFCMTokenRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update user's FCM token for push notifications."""
    await user_service.update_fcm_token(db, current_user, request.fcm_token)
    return MessageResponse(message="FCM token updated successfully")


@router.post("/me/change-password", response_model=MessageResponse)
async def change_password(
    request: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Change current user's password."""
    await user_service.change_password(
        db,
        current_user,
        current_password=request.current_password,
        new_password=request.new_password,
    )
    return MessageResponse(message="Password changed successfully")


@router.delete("/me", response_model=MessageResponse)
async def delete_current_user(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete current user's account."""
    await user_service.deactivate_account(db, current_user)
    return MessageResponse(message="Account deleted successfully")
