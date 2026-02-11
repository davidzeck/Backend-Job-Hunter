"""
Alert service - business logic for user job alert management.

Alerts are the connection between scraped jobs and users.
They track: was the user notified? Did they read it? Did they apply?

This service handles reading/updating alerts.
Creating alerts is done by NotificationService (triggered by scrapers).
"""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.repositories.alert_repository import AlertRepository
from app.schemas.alert import AlertResponse
from app.schemas.job import JobListItem, CompanyBrief
from app.schemas.base import PaginatedResponse, MessageResponse


class AlertService:
    """Handles alert listing, reading, saving, and applying."""

    def __init__(self):
        self.alert_repo = AlertRepository()

    async def list_alerts(
        self,
        db: AsyncSession,
        user_id: UUID,
        *,
        unread_only: bool = False,
        page: int = 1,
        limit: int = 20,
    ) -> PaginatedResponse[AlertResponse]:
        """Get paginated alerts for a user."""
        is_read = False if unread_only else None

        alerts, total = await self.alert_repo.find_for_user(
            db,
            user_id,
            is_read=is_read,
            page=page,
            limit=limit,
        )

        items = [self._to_response(alert) for alert in alerts]

        return PaginatedResponse(
            items=items,
            total=total,
            page=page,
            limit=limit,
            pages=(total + limit - 1) // limit if total > 0 else 0,
        )

    async def mark_read(
        self,
        db: AsyncSession,
        user_id: UUID,
        alert_id: UUID,
    ) -> MessageResponse:
        """Mark a single alert as read."""
        alert = await self._get_user_alert(db, user_id, alert_id)
        alert.is_read = True
        await db.commit()
        return MessageResponse(message="Alert marked as read")

    async def toggle_saved(
        self,
        db: AsyncSession,
        user_id: UUID,
        alert_id: UUID,
    ) -> MessageResponse:
        """Toggle saved status for an alert."""
        alert = await self._get_user_alert(db, user_id, alert_id)
        alert.is_saved = not alert.is_saved
        await db.commit()
        status = "saved" if alert.is_saved else "unsaved"
        return MessageResponse(message=f"Alert {status}")

    async def mark_applied(
        self,
        db: AsyncSession,
        user_id: UUID,
        alert_id: UUID,
    ) -> MessageResponse:
        """Mark an alert as applied (user applied to the job)."""
        alert = await self._get_user_alert(db, user_id, alert_id)
        alert.is_applied = True
        alert.applied_at = datetime.now(timezone.utc)
        await db.commit()
        return MessageResponse(message="Alert marked as applied")

    async def _get_user_alert(self, db, user_id: UUID, alert_id: UUID):
        """
        Fetch an alert ensuring it belongs to the requesting user.

        Why check user_id? Authorization. Without it, any authenticated
        user could modify any other user's alerts by guessing alert IDs.
        This is the IDOR (Insecure Direct Object Reference) prevention.
        """
        alert = await self.alert_repo.get_by_id(db, alert_id)

        if not alert or alert.user_id != user_id:
            raise NotFoundException("Alert not found")

        return alert

    def _to_response(self, alert) -> AlertResponse:
        """Convert alert model to response schema."""
        return AlertResponse(
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
