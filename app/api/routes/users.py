"""
User routes.

Thin controllers - all business logic lives in UserService / CVService.
"""
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, delete as sa_delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.user_skill import UserSkill
from app.services.user_service import UserService
from app.services.cv_service import CVService
from app.schemas.user import (
    UserResponse,
    UserProfileResponse,
    UserUpdate,
    UpdateFCMTokenRequest,
)
from app.schemas.auth import ChangePasswordRequest
from app.schemas.base import MessageResponse
from app.schemas.cv import (
    CVPresignRequest,
    CVPresignResponse,
    CVConfirmRequest,
    CVResponse,
    CVDownloadUrlResponse,
)


class AddSkillRequest(BaseModel):
    skill: str

router = APIRouter(prefix="/users", tags=["users"])

user_service = UserService()
cv_service = CVService()


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


# ── Skills ────────────────────────────────────────────────────────────────────

@router.get("/me/skills", response_model=List[str])
async def get_user_skills(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all skill names for the current user."""
    result = await db.execute(
        select(UserSkill.skill_name).where(UserSkill.user_id == current_user.id)
    )
    return result.scalars().all()


@router.post("/me/skills", response_model=MessageResponse, status_code=201)
async def add_user_skill(
    req: AddSkillRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually add a skill (source='manual'). Upserts on conflict."""
    skill = req.skill.strip()
    if not skill:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Skill name is required.")
    stmt = (
        pg_insert(UserSkill)
        .values(user_id=current_user.id, skill_name=skill, source="manual")
        .on_conflict_do_nothing(constraint="uq_user_skill")
    )
    await db.execute(stmt)
    await db.commit()
    return MessageResponse(message=f"Skill '{skill}' added.")


@router.delete("/me/skills/{skill_name}", response_model=MessageResponse)
async def remove_user_skill(
    skill_name: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a skill by name."""
    result = await db.execute(
        sa_delete(UserSkill).where(
            UserSkill.user_id == current_user.id,
            UserSkill.skill_name == skill_name,
        )
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found.")
    return MessageResponse(message=f"Skill '{skill_name}' removed.")


# ── CV Management ────────────────────────────────────────────────────────────

@router.post("/me/cv/presign", response_model=CVPresignResponse, status_code=201)
async def presign_cv_upload(
    req: CVPresignRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Step 1 — Request a presigned S3 POST URL for direct CV upload.

    The client validates file size / type locally, then calls this endpoint.
    Response contains a short-lived upload_url + fields the client must POST
    directly to S3 (never through this server).
    After S3 returns 204, call POST /me/cv/{cv_id}/confirm.
    """
    return await cv_service.presign_upload(db, current_user.id, req)


@router.post("/me/cv/{cv_id}/confirm", response_model=CVResponse)
async def confirm_cv_upload(
    cv_id: uuid.UUID,
    req: CVConfirmRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Step 3 — Confirm the S3 upload completed and trigger background processing.

    Verifies S3 object existence and hash integrity, then enqueues the Celery
    process_cv task (text extraction + skill matching).
    The returned CV record will have upload_status="uploaded" briefly, then
    "processing", then "ready" once Celery finishes.
    """
    return await cv_service.confirm_upload(db, current_user.id, cv_id, req)


@router.get("/me/cv", response_model=list[CVResponse])
async def list_cvs(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all active CVs for the current user, most recently uploaded first."""
    return await cv_service.list_cvs(db, current_user.id)


@router.get("/me/cv/{cv_id}/download-url", response_model=CVDownloadUrlResponse)
async def get_cv_download_url(
    cv_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get a time-limited presigned S3 URL for downloading a CV.

    The file is served directly from S3/CDN — no bytes pass through this server.
    The URL expires after ~1 hour.
    """
    return await cv_service.get_download_url(db, current_user.id, cv_id)


@router.delete("/me/cv/{cv_id}", response_model=MessageResponse)
async def delete_cv(
    cv_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Soft-delete a CV record and remove its file from S3.

    All skills extracted from this CV are also deleted (cascade).
    """
    await cv_service.delete_cv(db, current_user.id, cv_id)
    return MessageResponse(message="CV deleted successfully")
