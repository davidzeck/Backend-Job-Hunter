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
from celery.schedules import crontab

from app.workers.celery_app import celery_app
from app.core.database import async_session_maker
# Single shared bridge — disposes the engine per task so pooled connections
# never leak across event loops (see run_async docstring in tasks.py).
from app.workers.tasks import run_async


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
    "revalidate-active-jobs": {
        "task": "app.workers.scheduler.revalidate_active_jobs",
        "schedule": crontab(hour=2, minute=0),  # before the 03:00 log cleanup
    },
    "cleanup-old-logs": {
        "task": "app.workers.scheduler.cleanup_old_scrape_logs",
        "schedule": crontab(hour=3, minute=0),
    },
    "cleanup-expired-cv-analyses": {
        "task": "app.workers.scheduler.cleanup_expired_cv_analyses",
        "schedule": crontab(hour=4, minute=0),
    },
    "cleanup-auth-tokens": {
        "task": "app.workers.scheduler.cleanup_auth_tokens",
        "schedule": crontab(hour=4, minute=30),
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
def revalidate_active_jobs():
    """Nightly staleness sweep: re-check apply-URL liveness for the oldest-validated
    active jobs; deactivate listings that read dead twice in a row."""
    return run_async(_revalidate_active_jobs())


async def _revalidate_active_jobs():
    from app.core.config import settings
    from app.services.validation_service import ValidationService

    if not settings.validation_enabled:
        return {"skipped": "validation_disabled"}

    async with async_session_maker() as db:
        return await ValidationService().revalidate_stale(db)


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


@celery_app.task
def cleanup_expired_cv_analyses():
    """Delete cv_analyses rows past their expires_at to prevent table bloat."""
    return run_async(_cleanup_expired_cv_analyses())


async def _cleanup_expired_cv_analyses():
    from datetime import datetime, timezone
    from sqlalchemy import delete
    from app.models.cv_analysis import CVAnalysis

    async with async_session_maker() as db:
        now = datetime.now(timezone.utc)
        result = await db.execute(
            delete(CVAnalysis).where(CVAnalysis.expires_at < now)
        )
        await db.commit()

        return {"deleted": result.rowcount}


@celery_app.task
def cleanup_auth_tokens():
    """
    Purge dead auth rows:
      - auth_sessions more than 1 day past expiry (replaced rows are kept
        until expiry so a late replay of a rotated token still trips family
        revocation — see AuthService.refresh)
      - email_tokens that are long expired or were used over a week ago
    """
    return run_async(_cleanup_auth_tokens())


async def _cleanup_auth_tokens():
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import delete, or_, and_
    from app.models.auth_session import AuthSession
    from app.models.email_token import EmailToken

    async with async_session_maker() as db:
        now = datetime.now(timezone.utc)

        sessions_result = await db.execute(
            delete(AuthSession).where(
                AuthSession.expires_at < now - timedelta(days=1)
            )
        )
        tokens_result = await db.execute(
            delete(EmailToken).where(
                or_(
                    EmailToken.expires_at < now - timedelta(days=7),
                    and_(
                        EmailToken.used_at.isnot(None),
                        EmailToken.used_at < now - timedelta(days=7),
                    ),
                )
            )
        )
        await db.commit()

        return {
            "sessions_deleted": sessions_result.rowcount,
            "email_tokens_deleted": tokens_result.rowcount,
        }
