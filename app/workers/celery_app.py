"""
Celery application configuration.

This module sets up the Celery app with Redis as broker and backend.
"""
from celery import Celery

from app.core.config import settings

# Create Celery app
celery_app = Celery(
    "job_scout",
    broker=settings.redis_url,
    backend=f"{settings.redis_url}/1",  # Use different DB for results
)

# Configure Celery
celery_app.conf.update(
    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # Timezone
    timezone="UTC",
    enable_utc=True,

    # Task settings
    task_track_started=True,
    task_time_limit=300,  # 5 minutes hard limit
    task_soft_time_limit=240,  # 4 minutes soft limit

    # Worker settings
    worker_prefetch_multiplier=1,  # Don't prefetch (scraping is slow)
    task_acks_late=True,  # Ack after completion for reliability
    worker_concurrency=2,  # Limit concurrent scrapers

    # Result settings
    result_expires=3600,  # Results expire after 1 hour

    # Retry settings
    task_default_retry_delay=60,  # 1 minute
    task_max_retries=3,
)

# Auto-discover tasks from workers module
celery_app.autodiscover_tasks(["app.workers"])
