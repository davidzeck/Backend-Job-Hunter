"""
Scrape service - orchestrates the full scraping pipeline.

This is the CORE BUSINESS LOGIC of Job Scout:
1. Load a source from the database
2. Run the appropriate scraper
3. Deduplicate results against existing jobs
4. Save new jobs
5. Log the scrape attempt
6. Return new job IDs for notification

WHY this is a service and not inline in the Celery task:
- The Celery task is an entry point (like a route). It should be thin.
- This logic needs to be testable without Celery running.
- If we ever switch from Celery to another queue (e.g., arq, dramatiq),
  the service doesn't change.
"""
from datetime import datetime, timezone
from typing import Dict, List
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.models.scrape_log import ScrapeLog
from app.repositories.source_repository import SourceRepository
from app.repositories.job_repository import JobRepository
from app.scrapers.registry import get_scraper


class ScrapeService:
    """Orchestrates job scraping for a single source."""

    def __init__(self):
        self.source_repo = SourceRepository()
        self.job_repo = JobRepository()

    async def scrape_source(
        self,
        db: AsyncSession,
        source_id: UUID,
    ) -> Dict:
        """
        Execute a full scrape cycle for one source.

        Returns:
            Dict with success status, counts, new_job_ids, and any error.
        """
        # 1. Load source
        source = await self.source_repo.get_by_id(db, source_id)

        if not source:
            return {"success": False, "error": "Source not found"}
        if not source.is_active:
            return {"success": False, "error": "Source is disabled"}

        # 2. Instantiate the correct scraper
        try:
            scraper = get_scraper(
                source.scraper_class,
                str(source.id),
                source.config,
            )
        except ValueError as e:
            return {"success": False, "error": str(e)}

        # 3. Execute scrape
        scrape_result = await scraper.execute()

        # 4. Process results
        new_job_ids: List[str] = []

        if scrape_result.success:
            for scraped_job in scrape_result.jobs:
                # Deduplication: check if job already exists by (source_id, external_id)
                existing = await self.job_repo.find_by_source_and_external_id(
                    db, source.id, scraped_job.external_id,
                )

                if existing:
                    # Update description if changed (job was re-posted with edits)
                    if existing.description != scraped_job.description:
                        existing.description = scraped_job.description
                else:
                    # New job discovered - this is what we exist for
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
                    await db.flush()  # Get the ID assigned
                    new_job_ids.append(str(job.id))

            # Update source health
            source.mark_success(len(scrape_result.jobs), len(new_job_ids))
        else:
            source.mark_failure(scrape_result.error)

        # 5. Always log the attempt (success or failure)
        log = ScrapeLog(
            source_id=source.id,
            status="success" if scrape_result.success else "failed",
            jobs_found=len(scrape_result.jobs) if scrape_result.success else 0,
            new_jobs=len(new_job_ids),
            duration_ms=scrape_result.duration_ms,
            error_message=scrape_result.error,
        )
        db.add(log)

        await db.commit()

        return {
            "success": scrape_result.success,
            "jobs_found": len(scrape_result.jobs) if scrape_result.success else 0,
            "new_jobs": len(new_job_ids),
            "new_job_ids": new_job_ids,
            "error": scrape_result.error,
        }

    async def get_due_sources(self, db: AsyncSession) -> List[str]:
        """
        Get IDs of sources that are due for scraping.

        Why return IDs instead of objects? Because the caller (Celery Beat)
        will send each ID as a separate task. The task then loads the source
        fresh from the DB - preventing stale data issues.
        """
        sources = await self.source_repo.get_due_sources(db)
        return [str(s.id) for s in sources]
