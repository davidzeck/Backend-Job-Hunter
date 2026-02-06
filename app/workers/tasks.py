"""
Celery tasks for background processing.

Main tasks:
- scrape_source: Scrape a job source
- notify_matching_users: Send notifications for new jobs
- process_cv: Process uploaded CV and extract skills
"""
import asyncio
from datetime import datetime, timezone
from typing import List
from uuid import UUID

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.workers.celery_app import celery_app
from app.core.database import async_session_maker
from app.scrapers.registry import get_scraper
from app.scrapers.base import ScrapedJob


def run_async(coro):
    """Helper to run async code in sync Celery tasks."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(bind=True, max_retries=3)
def scrape_source(self, source_id: str):
    """
    Scrape a single job source and process results.

    Flow:
    1. Load source config from DB
    2. Instantiate appropriate scraper
    3. Execute scrape
    4. Deduplicate against existing jobs
    5. Save new jobs
    6. Trigger notifications for new job matches

    Args:
        source_id: UUID of the job source to scrape
    """
    return run_async(_scrape_source_async(self, source_id))


async def _scrape_source_async(task, source_id: str):
    """Async implementation of scrape_source."""
    from app.models.job_source import JobSource
    from app.models.job import Job
    from app.models.scrape_log import ScrapeLog

    async with async_session_maker() as db:
        # Load source
        result = await db.execute(
            select(JobSource).where(JobSource.id == source_id)
        )
        source = result.scalar_one_or_none()

        if not source:
            return {"error": "Source not found"}

        if not source.is_active:
            return {"error": "Source is disabled"}

        # Get scraper
        try:
            scraper = get_scraper(
                source.scraper_class,
                str(source.id),
                source.config,
            )
        except ValueError as e:
            return {"error": str(e)}

        # Execute scrape
        scrape_result = await scraper.execute()

        # Create scrape log
        log = ScrapeLog(
            source_id=source.id,
            status="success" if scrape_result.success else "failed",
            jobs_found=len(scrape_result.jobs) if scrape_result.success else 0,
            duration_ms=scrape_result.duration_ms,
            error_message=scrape_result.error,
        )

        if scrape_result.success:
            # Process jobs
            new_jobs = []

            for scraped_job in scrape_result.jobs:
                # Check if job already exists
                existing = await db.execute(
                    select(Job).where(
                        Job.source_id == source.id,
                        Job.external_id == scraped_job.external_id,
                    )
                )
                existing_job = existing.scalar_one_or_none()

                if existing_job:
                    # Update if description changed
                    if existing_job.description != scraped_job.description:
                        existing_job.description = scraped_job.description
                        existing_job.updated_at = datetime.now(timezone.utc)
                else:
                    # Create new job
                    job = Job(
                        source_id=source.id,
                        company_id=source.company_id,
                        external_id=scraped_job.external_id,
                        title=scraped_job.title,
                        description=scraped_job.description,
                        location=scraped_job.location,
                        location_type=scraped_job.location_type,
                        job_type=scraped_job.job_type,
                        seniority_level=scraped_job.seniority_level,
                        apply_url=scraped_job.apply_url,
                        posted_at=scraped_job.posted_at,
                        discovered_at=datetime.now(timezone.utc),
                        salary_min=scraped_job.salary_min,
                        salary_max=scraped_job.salary_max,
                        salary_currency=scraped_job.salary_currency,
                        raw_data=scraped_job.raw_data,
                    )
                    db.add(job)
                    new_jobs.append(job)

            # Update log with new jobs count
            log.new_jobs = len(new_jobs)

            # Update source health
            source.mark_success(len(scrape_result.jobs), len(new_jobs))

            # Commit all changes
            await db.commit()

            # Trigger notifications for new jobs
            if new_jobs:
                for job in new_jobs:
                    await db.refresh(job)  # Get the ID
                    notify_matching_users.delay(str(job.id))

        else:
            # Mark failure
            source.mark_failure(scrape_result.error)

        # Save log
        db.add(log)
        await db.commit()

        return {
            "success": scrape_result.success,
            "jobs_found": len(scrape_result.jobs) if scrape_result.success else 0,
            "new_jobs": log.new_jobs,
            "error": scrape_result.error,
        }


@celery_app.task(bind=True, max_retries=3)
def notify_matching_users(self, job_id: str):
    """
    Find users matching a new job and send notifications.

    CRITICAL: Notifications fire immediately (no batching).
    This is the competitive moat - speed to notification.

    Args:
        job_id: UUID of the new job
    """
    return run_async(_notify_matching_users_async(self, job_id))


async def _notify_matching_users_async(task, job_id: str):
    """Async implementation of notify_matching_users."""
    from app.models.job import Job
    from app.models.user import User
    from app.models.user_job_alert import UserJobAlert
    from sqlalchemy.orm import selectinload

    async with async_session_maker() as db:
        # Load job with company
        result = await db.execute(
            select(Job)
            .options(selectinload(Job.company))
            .where(Job.id == job_id)
        )
        job = result.scalar_one_or_none()

        if not job:
            return {"error": "Job not found"}

        # Find matching users
        # This query checks user preferences against job attributes
        users_result = await db.execute(
            select(User).where(
                User.is_active == True,
                User.fcm_token.isnot(None),  # Has push token
            )
        )
        all_users = users_result.scalars().all()

        matching_users = []
        for user in all_users:
            if _user_matches_job(user, job):
                # Check if already notified
                existing = await db.execute(
                    select(UserJobAlert).where(
                        UserJobAlert.user_id == user.id,
                        UserJobAlert.job_id == job.id,
                    )
                )
                if not existing.scalar_one_or_none():
                    matching_users.append(user)

        # Send notifications
        notifications_sent = 0
        for user in matching_users:
            try:
                # Create alert record first (idempotency)
                alert = UserJobAlert(
                    user_id=user.id,
                    job_id=job.id,
                    notified_at=datetime.now(timezone.utc),
                    notification_channel="push",
                )
                db.add(alert)
                await db.flush()

                # Send push notification
                success = await _send_push_notification(
                    user=user,
                    job=job,
                )

                if success:
                    alert.is_delivered = True
                    notifications_sent += 1

            except Exception as e:
                print(f"Failed to notify user {user.id}: {e}")
                continue

        await db.commit()

        return {
            "job_id": job_id,
            "matching_users": len(matching_users),
            "notifications_sent": notifications_sent,
        }


def _user_matches_job(user, job) -> bool:
    """
    Check if a user's preferences match a job.

    Args:
        user: User model instance
        job: Job model instance

    Returns:
        True if user should be notified about this job
    """
    prefs = user.preferences or {}

    # Check notification settings
    notifications = prefs.get("notifications", {})
    if not notifications.get("push", True):
        return False

    # Check company filter
    companies = prefs.get("companies", [])
    if companies and job.company.slug not in companies:
        return False

    # Check role filter
    roles = prefs.get("roles", [])
    if roles:
        job_title_lower = job.title.lower()
        role_match = any(role.replace("_", " ") in job_title_lower for role in roles)
        if not role_match:
            return False

    # Check location filter
    locations = prefs.get("locations", [])
    if locations:
        job_location_lower = (job.location or "").lower()
        location_match = any(loc in job_location_lower for loc in locations)

        # Also match remote jobs if user wants remote
        if "remote" in locations and job.location_type == "remote":
            location_match = True

        if not location_match:
            return False

    return True


async def _send_push_notification(user, job) -> bool:
    """
    Send a push notification to a user about a new job.

    Args:
        user: User model instance
        job: Job model instance

    Returns:
        True if notification was sent successfully
    """
    # TODO: Implement actual FCM sending
    # For now, just log and return True
    print(f"Would send push to {user.email}: {job.title} at {job.company.name}")
    return True


@celery_app.task(bind=True, max_retries=2)
def process_cv(self, user_id: str, cv_id: str):
    """
    Process an uploaded CV and extract skills.

    Args:
        user_id: UUID of the user
        cv_id: UUID of the CV record
    """
    return run_async(_process_cv_async(self, user_id, cv_id))


async def _process_cv_async(task, user_id: str, cv_id: str):
    """Async implementation of process_cv."""
    from app.models.user_cv import UserCV
    from app.models.user_skill import UserSkill

    async with async_session_maker() as db:
        # Load CV
        result = await db.execute(
            select(UserCV).where(
                UserCV.id == cv_id,
                UserCV.user_id == user_id,
            )
        )
        cv = result.scalar_one_or_none()

        if not cv:
            return {"error": "CV not found"}

        # TODO: Implement actual CV processing
        # 1. Read PDF from file_path
        # 2. Extract text using pdfplumber
        # 3. Extract skills using skill extractor
        # 4. Save skills to database

        # Mark as processed
        cv.processed_at = datetime.now(timezone.utc)
        await db.commit()

        return {
            "cv_id": cv_id,
            "status": "processed",
        }
