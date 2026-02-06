"""
Workers package - Celery tasks and background processing.
"""
from app.workers.celery_app import celery_app
from app.workers.tasks import (
    scrape_source,
    notify_matching_users,
    process_cv,
)
from app.workers.scheduler import (
    check_scraper_health,
    scrape_all_active_sources,
    cleanup_old_scrape_logs,
)

__all__ = [
    "celery_app",
    "scrape_source",
    "notify_matching_users",
    "process_cv",
    "check_scraper_health",
    "scrape_all_active_sources",
    "cleanup_old_scrape_logs",
]
