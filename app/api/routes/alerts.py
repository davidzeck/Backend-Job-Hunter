"""
Alert routes.

Thin controllers - AlertService handles authorization checks,
response construction, and state transitions.
"""
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.services.alert_service import AlertService
from app.schemas.alert import AlertResponse
from app.schemas.base import PaginatedResponse, MessageResponse

router = APIRouter(prefix="/alerts", tags=["alerts"])

alert_service = AlertService()


@router.get("/", response_model=PaginatedResponse[AlertResponse])
async def list_alerts(
    unread_only: bool = Query(False, description="Show only unread alerts"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List user's job alerts."""
    return await alert_service.list_alerts(
        db,
        current_user.id,
        unread_only=unread_only,
        page=page,
        limit=limit,
    )


@router.patch("/{alert_id}/read", response_model=MessageResponse)
async def mark_alert_read(
    alert_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark an alert as read."""
    return await alert_service.mark_read(db, current_user.id, alert_id)


@router.patch("/{alert_id}/saved", response_model=MessageResponse)
async def toggle_alert_saved(
    alert_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Toggle saved status for an alert."""
    return await alert_service.toggle_saved(db, current_user.id, alert_id)


@router.patch("/{alert_id}/applied", response_model=MessageResponse)
async def mark_alert_applied(
    alert_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark an alert as applied."""
    return await alert_service.mark_applied(db, current_user.id, alert_id)
