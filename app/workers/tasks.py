"""
Celery tasks for background processing.

ARCHITECTURE RULE: Same as routes - tasks are thin entry points.
They do exactly 3 things:
  1. Create a DB session (since we're outside FastAPI's request cycle)
  2. Call a service method
  3. Return the result

NO raw SQL queries. NO business logic. NO model imports (except through services).
"""
import asyncio

from app.workers.celery_app import celery_app
from app.core.database import async_session_maker


def run_async(coro):
    """
    Helper to run async code in sync Celery tasks.

    Why is this needed? Celery workers are synchronous. Our services
    are async (because SQLAlchemy async requires it). This bridge
    creates an event loop, runs the coroutine, and cleans up.
    """
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

    This is the most important task in the system. The pipeline:
      Source -> Scraper -> Dedupe -> Save -> Notify

    Args:
        source_id: UUID of the job source to scrape
    """
    return run_async(_scrape_source(source_id))


async def _scrape_source(source_id: str):
    """Async implementation - delegates to ScrapeService."""
    from app.services.scrape_service import ScrapeService

    scrape_service = ScrapeService()

    async with async_session_maker() as db:
        result = await scrape_service.scrape_source(db, source_id)

        # If new jobs were found, trigger notifications immediately.
        # Why .delay() instead of calling directly? Because notification
        # for each job is independent work that can run in parallel
        # on different workers. This is the fan-out pattern.
        if result.get("new_job_ids"):
            for job_id in result["new_job_ids"]:
                notify_matching_users.delay(job_id)

        return result


@celery_app.task(bind=True, max_retries=3)
def notify_matching_users(self, job_id: str):
    """
    Find users matching a new job and send notifications.

    CRITICAL PATH: This fires immediately after a job is discovered.
    Speed here = competitive advantage. No batching, no delays.

    Args:
        job_id: UUID of the new job
    """
    return run_async(_notify_matching_users(job_id))


async def _notify_matching_users(job_id: str):
    """Async implementation - delegates to NotificationService."""
    from app.services.notification_service import NotificationService

    notification_service = NotificationService()

    async with async_session_maker() as db:
        return await notification_service.notify_for_new_job(db, job_id)


@celery_app.task(bind=True, max_retries=2)
def process_cv(self, user_id: str, cv_id: str):
    """
    Process an uploaded CV and extract skills.

    TODO: Implement CV text extraction with pdfplumber
    and skill matching against a skills taxonomy.

    Args:
        user_id: UUID of the user
        cv_id: UUID of the CV record
    """
    return run_async(_process_cv(user_id, cv_id))


async def _process_cv(user_id: str, cv_id: str):
    """Async implementation - placeholder for CV processing."""
    from datetime import datetime, timezone
    from sqlalchemy import select
    from app.models.user_cv import UserCV

    async with async_session_maker() as db:
        result = await db.execute(
            select(UserCV).where(
                UserCV.id == cv_id,
                UserCV.user_id == user_id,
            )
        )
        cv = result.scalar_one_or_none()

        if not cv:
            return {"error": "CV not found"}

        # TODO: Implement actual processing:
        # 1. Read PDF with pdfplumber
        # 2. Extract text
        # 3. Match skills against taxonomy
        # 4. Save UserSkill records

        cv.processed_at = datetime.now(timezone.utc)
        await db.commit()

        return {"cv_id": cv_id, "status": "processed"}
