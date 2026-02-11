"""
Celery Beat scheduler configuration.

Defines periodic tasks that run on a schedule:
- Health checks every 5 minutes
- Scrape all due sources every 15 minutes
- Cleanup old logs daily

WHY Celery Beat instead of cron?
- Beat is declarative (defined in Python, not crontab files)
- Beat respects task routing (scrape tasks go to scrape queue)
- Beat is version-controlled with the code
- Beat runs inside Docker, no host system dependency
"""
import asyncio

from celery.schedules import crontab

from app.workers.celery_app import celery_app
from app.core.database import async_session_maker


def run_async(coro):
    """Helper to run async code in sync Celery tasks."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─── Periodic Task Schedule ────────────────────────────────────

celery_app.conf.beat_schedule = {
    "check-scraper-health": {
        "task": "app.workers.scheduler.check_scraper_health",
        "schedule": crontab(minute="*/5"),
    },
    "scrape-all-sources": {
        "task": "app.workers.scheduler.scrape_all_active_sources",
        "schedule": crontab(minute="*/15"),
    },
    "cleanup-old-logs": {
        "task": "app.workers.scheduler.cleanup_old_scrape_logs",
        "schedule": crontab(hour=3, minute=0),
    },
}


# ─── Scheduled Tasks ──────────────────────────────────────────

@celery_app.task
def check_scraper_health():
    """Check health of all scrapers and alert if any are failing."""
    return run_async(_check_scraper_health())


async def _check_scraper_health():
    from app.repositories.source_repository import SourceRepository

    source_repo = SourceRepository()

    async with async_session_maker() as db:
        failing = await source_repo.get_failing_sources(db)

        if failing:
            # TODO: Send alert to admin (email, Slack, etc.)
            print(f"WARNING: {len(failing)} scrapers are failing!")
            for source in failing:
                print(f"  - Source {source.id}: {source.consecutive_failures} failures")

        return {"failing_count": len(failing)}


@celery_app.task
def scrape_all_active_sources():
    """
    Trigger scraping for all active sources that are due.

    This is the orchestrator. It doesn't scrape itself - it dispatches
    individual scrape_source tasks. This is the fan-out pattern:
    one scheduler task -> N scraper tasks running in parallel.
    """
    return run_async(_scrape_all_active_sources())


async def _scrape_all_active_sources():
    from app.services.scrape_service import ScrapeService
    from app.workers.tasks import scrape_source

    scrape_service = ScrapeService()

    async with async_session_maker() as db:
        due_source_ids = await scrape_service.get_due_sources(db)

        for source_id in due_source_ids:
            scrape_source.delay(source_id)

        return {"triggered": len(due_source_ids)}


@celery_app.task
def cleanup_old_scrape_logs():
    """Delete scrape logs older than 30 days to prevent table bloat."""
    return run_async(_cleanup_old_scrape_logs())


async def _cleanup_old_scrape_logs():
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import delete
    from app.models.scrape_log import ScrapeLog

    async with async_session_maker() as db:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)

        result = await db.execute(
            delete(ScrapeLog).where(ScrapeLog.created_at < cutoff)
        )
        await db.commit()

        return {"deleted": result.rowcount}
