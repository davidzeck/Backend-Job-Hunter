"""
Alert routes.
"""
from datetime import datetime, timezone
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.exceptions import NotFoundException
from app.api.deps import get_current_user
from app.models.user import User
from app.models.user_job_alert import UserJobAlert
from app.models.job import Job
from app.schemas.alert import AlertResponse
from app.schemas.job import JobListItem, CompanyBrief
from app.schemas.base import PaginatedResponse, MessageResponse

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("/", response_model=PaginatedResponse[AlertResponse])
async def list_alerts(
    unread_only: bool = Query(False, description="Show only unread alerts"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    List user's job alerts.
    """
    # Base query
    query = (
        select(UserJobAlert)
        .options(selectinload(UserJobAlert.job).selectinload(Job.company))
        .where(UserJobAlert.user_id == current_user.id)
    )

    if unread_only:
        query = query.where(UserJobAlert.is_read == False)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination
    query = query.order_by(UserJobAlert.notified_at.desc())
    query = query.offset((page - 1) * limit).limit(limit)

    result = await db.execute(query)
    alerts = result.scalars().all()

    # Transform to response
    items = [
        AlertResponse(
            id=alert.id,
            job=JobListItem(
                id=alert.job.id,
                title=alert.job.title,
                company=CompanyBrief(
                    id=alert.job.company.id,
                    name=alert.job.company.name,
                    slug=alert.job.company.slug,
                    logo_url=alert.job.company.logo_url,
                ),
                location=alert.job.location,
                location_type=alert.job.location_type,
                job_type=alert.job.job_type,
                apply_url=alert.job.apply_url,
                posted_at=alert.job.posted_at,
                discovered_at=alert.job.discovered_at,
                is_active=alert.job.is_active,
            ),
            notified_at=alert.notified_at,
            notification_channel=alert.notification_channel,
            is_delivered=alert.is_delivered,
            is_read=alert.is_read,
            is_saved=alert.is_saved,
            is_applied=alert.is_applied,
            applied_at=alert.applied_at,
        )
        for alert in alerts
    ]

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        limit=limit,
        pages=(total + limit - 1) // limit,
    )


@router.patch("/{alert_id}/read", response_model=MessageResponse)
async def mark_alert_read(
    alert_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Mark an alert as read.
    """
    result = await db.execute(
        select(UserJobAlert).where(
            UserJobAlert.id == alert_id,
            UserJobAlert.user_id == current_user.id,
        )
    )
    alert = result.scalar_one_or_none()

    if not alert:
        raise NotFoundException("Alert not found")

    alert.is_read = True
    await db.commit()

    return MessageResponse(message="Alert marked as read")


@router.patch("/{alert_id}/saved", response_model=MessageResponse)
async def toggle_alert_saved(
    alert_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Toggle saved status for an alert.
    """
    result = await db.execute(
        select(UserJobAlert).where(
            UserJobAlert.id == alert_id,
            UserJobAlert.user_id == current_user.id,
        )
    )
    alert = result.scalar_one_or_none()

    if not alert:
        raise NotFoundException("Alert not found")

    alert.is_saved = not alert.is_saved
    await db.commit()

    status = "saved" if alert.is_saved else "unsaved"
    return MessageResponse(message=f"Alert {status}")


@router.patch("/{alert_id}/applied", response_model=MessageResponse)
async def mark_alert_applied(
    alert_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Mark an alert as applied.
    """
    result = await db.execute(
        select(UserJobAlert).where(
            UserJobAlert.id == alert_id,
            UserJobAlert.user_id == current_user.id,
        )
    )
    alert = result.scalar_one_or_none()

    if not alert:
        raise NotFoundException("Alert not found")

    alert.is_applied = True
    alert.applied_at = datetime.now(timezone.utc)
    await db.commit()

    return MessageResponse(message="Alert marked as applied")
