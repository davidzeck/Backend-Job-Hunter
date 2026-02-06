"""
Celery Beat scheduler configuration.

Defines periodic tasks for scraping job sources.
"""
from celery.schedules import crontab

from app.workers.celery_app import celery_app

# Periodic task schedule
celery_app.conf.beat_schedule = {
    # Health check - every 5 minutes
    "check-scraper-health": {
        "task": "app.workers.scheduler.check_scraper_health",
        "schedule": crontab(minute="*/5"),
    },

    # Note: Individual source scraping is configured dynamically
    # based on each source's scrape_interval_minutes setting.
    # See the scrape_all_active_sources task below.

    # Scrape all active sources - every 15 minutes
    # This task will respect each source's individual interval
    "scrape-all-sources": {
        "task": "app.workers.scheduler.scrape_all_active_sources",
        "schedule": crontab(minute="*/15"),
    },

    # Cleanup old scrape logs - daily at 3 AM
    "cleanup-old-logs": {
        "task": "app.workers.scheduler.cleanup_old_scrape_logs",
        "schedule": crontab(hour=3, minute=0),
    },
}


@celery_app.task
def check_scraper_health():
    """
    Check health of all scrapers and send alerts if needed.
    """
    import asyncio
    from sqlalchemy import select
    from app.core.database import async_session_maker
    from app.models.job_source import JobSource

    async def _check():
        async with async_session_maker() as db:
            result = await db.execute(
                select(JobSource).where(JobSource.health_status == "failing")
            )
            failing_sources = result.scalars().all()

            if failing_sources:
                # TODO: Send alert to admin
                print(f"WARNING: {len(failing_sources)} scrapers are failing!")
                for source in failing_sources:
                    print(f"  - Source {source.id}: {source.consecutive_failures} failures")

            return {"failing_count": len(failing_sources)}

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_check())
    finally:
        loop.close()


@celery_app.task
def scrape_all_active_sources():
    """
    Trigger scraping for all active sources that are due.
    """
    import asyncio
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select, or_
    from app.core.database import async_session_maker
    from app.models.job_source import JobSource
    from app.workers.tasks import scrape_source

    async def _scrape_all():
        async with async_session_maker() as db:
            now = datetime.now(timezone.utc)

            # Find sources that are:
            # 1. Active
            # 2. Either never scraped, or due based on their interval
            result = await db.execute(
                select(JobSource).where(
                    JobSource.is_active == True,
                    or_(
                        JobSource.last_scraped_at.is_(None),
                        JobSource.last_scraped_at < now - timedelta(minutes=15),
                    ),
                )
            )
            sources = result.scalars().all()

            triggered = 0
            for source in sources:
                # Check if enough time has passed based on source's interval
                if source.last_scraped_at:
                    interval = timedelta(minutes=source.scrape_interval_minutes)
                    if now - source.last_scraped_at < interval:
                        continue

                # Trigger scrape
                scrape_source.delay(str(source.id))
                triggered += 1

            return {"triggered": triggered}

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_scrape_all())
    finally:
        loop.close()


@celery_app.task
def cleanup_old_scrape_logs():
    """
    Delete scrape logs older than 30 days.
    """
    import asyncio
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import delete
    from app.core.database import async_session_maker
    from app.models.scrape_log import ScrapeLog

    async def _cleanup():
        async with async_session_maker() as db:
            cutoff = datetime.now(timezone.utc) - timedelta(days=30)

            result = await db.execute(
                delete(ScrapeLog).where(ScrapeLog.created_at < cutoff)
            )
            await db.commit()

            return {"deleted": result.rowcount}

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_cleanup())
    finally:
        loop.close()
