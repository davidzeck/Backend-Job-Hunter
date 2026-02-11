"""
Notification service - handles job matching and push notification delivery.

This is the CRITICAL PATH for Job Scout's competitive advantage:
Speed from job discovery to user notification.
"""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.models.user import User
from app.repositories.job_repository import JobRepository
from app.repositories.user_repository import UserRepository
from app.repositories.alert_repository import AlertRepository


class NotificationService:
    """
    Matches new jobs to users and dispatches notifications.

    Flow:
    1. New job discovered by scraper
    2. Service finds all users whose preferences match
    3. Creates alert records (idempotency)
    4. Sends push notifications via Firebase
    """

    def __init__(self):
        self.job_repo = JobRepository()
        self.user_repo = UserRepository()
        self.alert_repo = AlertRepository()

    async def notify_for_new_job(
        self,
        db: AsyncSession,
        job_id: UUID,
    ) -> dict:
        """
        Find matching users for a new job and send notifications.

        Returns:
            Dict with matching_users count and notifications_sent count.
        """
        job = await self.job_repo.get_with_company(db, job_id)
        if not job:
            return {"error": "Job not found"}

        # Get all notifiable users
        all_users = await self.user_repo.get_notifiable_users(db)

        # Filter to matching users
        matching_users = []
        for user in all_users:
            if self._user_matches_job(user, job):
                # Check idempotency - not already notified
                existing = await self.alert_repo.find_by_user_and_job(
                    db, user.id, job.id
                )
                if not existing:
                    matching_users.append(user)

        # Send notifications
        notifications_sent = 0
        for user in matching_users:
            try:
                # Create alert record first (idempotency)
                await self.alert_repo.create(
                    db,
                    user_id=user.id,
                    job_id=job.id,
                    notified_at=datetime.now(timezone.utc),
                    notification_channel="push",
                )
                await db.flush()

                # Send push notification
                success = await self._send_push(user, job)
                if success:
                    notifications_sent += 1

            except Exception as e:
                print(f"Failed to notify user {user.id}: {e}")
                continue

        await db.commit()

        return {
            "job_id": str(job_id),
            "matching_users": len(matching_users),
            "notifications_sent": notifications_sent,
        }

    def _user_matches_job(self, user: User, job: Job) -> bool:
        """
        Check if a user's preferences match a job.

        Matching criteria (all must pass if configured):
        - Push notifications enabled
        - Company in user's company watchlist
        - Job title matches user's role preferences
        - Location matches user's location preferences
        """
        prefs = user.preferences or {}

        # Quick exit: push disabled
        notifications = prefs.get("notifications", {})
        if not notifications.get("push", True):
            return False

        # Company filter
        companies = prefs.get("companies", [])
        if companies and job.company.slug not in companies:
            return False

        # Role filter (keyword matching)
        roles = prefs.get("roles", [])
        if roles:
            title_lower = job.title.lower()
            if not any(role.replace("_", " ") in title_lower for role in roles):
                return False

        # Location filter
        locations = prefs.get("locations", [])
        if locations:
            job_loc = (job.location or "").lower()
            location_match = any(loc in job_loc for loc in locations)

            # Always match remote jobs if user wants remote
            if "remote" in locations and job.location_type == "remote":
                location_match = True

            if not location_match:
                return False

        return True

    async def _send_push(self, user: User, job: Job) -> bool:
        """
        Send a push notification via Firebase Cloud Messaging.

        TODO: Implement actual FCM sending with firebase-admin.
        """
        # Placeholder - log and return True
        print(
            f"PUSH -> {user.email}: "
            f"New {job.title} at {job.company.name} ({job.location})"
        )
        return True
