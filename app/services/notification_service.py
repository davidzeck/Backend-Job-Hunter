"""
Notification service - handles job matching and push notification delivery.

This is the CRITICAL PATH for Job Scout's competitive advantage:
Speed from job discovery to user notification.
"""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.push import PushMessage, PushOutcome, send_push_messages
from app.models.job import Job
from app.models.user import User
from app.repositories.job_repository import JobRepository
from app.repositories.user_repository import UserRepository
from app.repositories.alert_repository import AlertRepository

logger = get_logger(__name__)


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

        # Prefetch skill context once (job skills + every candidate's skills) so
        # skill-aware matching stays O(1) queries instead of N+1.
        job_skill_rows = await self.job_repo.get_job_skills(db, job.id)
        job_skill_names = {s.skill_name.lower() for s in job_skill_rows}
        user_skill_map = await self.user_repo.get_skills_for_users(
            db, [u.id for u in all_users]
        )

        # Filter to matching users (preference match OR skill coverage)
        candidates = [
            u
            for u in all_users
            if self._user_matches_job(
                u, job, job_skill_names, user_skill_map.get(u.id, set())
            )
        ]

        # Idempotency: exclude already-alerted users in ONE query (was per-user)
        already_alerted = await self.alert_repo.find_alerted_user_ids(
            db, job.id, [u.id for u in candidates]
        )
        matching_users = [u for u in candidates if u.id not in already_alerted]

        # Create alert records first (idempotency anchor), then send one batch
        alerts = []
        for user in matching_users:
            alert = await self.alert_repo.create(
                db,
                user_id=user.id,
                job_id=job.id,
                notified_at=datetime.now(timezone.utc),
                notification_channel="push",
            )
            alerts.append((user, alert))
        await db.flush()

        outcomes = await send_push_messages(
            [self._build_push_message(user, alert, job) for user, alert in alerts]
        )

        notifications_sent = 0
        for (user, alert), outcome in zip(alerts, outcomes):
            if outcome is PushOutcome.SENT:
                alert.is_delivered = True
                notifications_sent += 1
            elif outcome is PushOutcome.DEAD_TOKEN:
                # FCM says this token is permanently gone — stop targeting it
                user.fcm_token = None
                logger.info("fcm_dead_token_cleared", user_id=str(user.id))

        await db.commit()

        return {
            "job_id": str(job_id),
            "matching_users": len(matching_users),
            "notifications_sent": notifications_sent,
        }

    # Skill-aware alerting: a job also matches when the user's CV skills cover
    # at least this fraction of the job's skills.
    _SKILL_COVERAGE_THRESHOLD = 0.5

    def _user_matches_job(
        self,
        user: User,
        job: Job,
        job_skill_names: set = frozenset(),
        user_skill_names: set = frozenset(),
    ) -> bool:
        """A user matches when push is enabled AND (their preferences match the
        job OR their CV skills cover it). Skill matching is opt-in per user
        (`skill_alerts_enabled`, default on)."""
        prefs = user.preferences or {}

        # Quick exit: push disabled
        if not prefs.get("notifications", {}).get("push", True):
            return False

        if self._matches_preferences(prefs, job):
            return True

        if prefs.get("skill_alerts_enabled", True) and job_skill_names and user_skill_names:
            coverage = len(job_skill_names & user_skill_names) / len(job_skill_names)
            if coverage >= self._SKILL_COVERAGE_THRESHOLD:
                return True

        return False

    @staticmethod
    def _matches_preferences(prefs: dict, job: Job) -> bool:
        """The original preference match: company watchlist, role keywords, and
        location — each applied only when configured (empty = no constraint)."""
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
            if "remote" in locations and job.location_type == "remote":
                location_match = True
            if not location_match:
                return False

        return True

    def _build_push_message(
        self, user: User, alert, job: Job
    ) -> PushMessage:
        """Notification block for system display + data payload for deep-linking."""
        body = job.company.name
        if job.location:
            body += f" · {job.location}"
        return PushMessage(
            token=user.fcm_token,
            title=f"New job: {job.title}",
            body=body,
            data={
                "type": "new_job",
                "job_id": str(job.id),
                "alert_id": str(alert.id),
            },
        )
