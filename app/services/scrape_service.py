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
from typing import Dict, List, Optional
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.job import Job
from app.models.scrape_log import ScrapeLog
from app.repositories.source_repository import SourceRepository
from app.repositories.job_repository import JobRepository
from app.scrapers.base import ScrapedJob
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

        # 4. Process results.
        #    new_job_ids = jobs cleared for alerting (structurally sane, not a
        #    cross-source duplicate). Structural-suspect and duplicate jobs are
        #    still stored — just flagged and kept out of the alert fan-out.
        new_job_ids: List[str] = []
        structural_suspect = 0
        duplicates = 0

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
                    continue

                # New job discovered - this is what we exist for.
                issues = (
                    self._structural_issues(scraped_job)
                    if settings.validation_enabled
                    else []
                )
                job = Job(
                    source_id=source.id,
                    company_id=source.company_id,
                    external_id=scraped_job.external_id,
                    title=(scraped_job.title or "")[:255],
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
                    validation_status="suspect" if issues else "unverified",
                )
                db.add(job)
                await db.flush()  # Get the ID assigned

                # Extract job skills from title+description (same taxonomy as CVs
                # → enables recommendations + a non-vacuous skill-gap endpoint).
                # Done for every new job, including suspect/duplicate ones.
                await self._extract_job_skills(db, job)

                if issues:
                    # Cheap, no-network quality gate — likely scraper parser drift.
                    job.validation_detail = {"structural_issues": issues}
                    structural_suspect += 1
                    continue

                if settings.validation_enabled:
                    dup = await self.job_repo.find_cross_source_duplicate(
                        db,
                        company_id=job.company_id,
                        source_id=job.source_id,
                        title=job.title,
                        exclude_job_id=job.id,
                    )
                    if dup:
                        job.duplicate_of_job_id = dup.id
                        duplicates += 1
                        continue  # user was already alerted for the original

                new_job_ids.append(str(job.id))

            # Update source health
            source.mark_success(len(scrape_result.jobs), len(new_job_ids))
        else:
            source.mark_failure(scrape_result.error)

        # 5. Always log the attempt (success or failure)
        extra_data = None
        if structural_suspect or duplicates:
            extra_data = {
                "structural_suspect": structural_suspect,
                "cross_source_duplicates": duplicates,
            }
        log = ScrapeLog(
            source_id=source.id,
            status="success" if scrape_result.success else "failed",
            jobs_found=len(scrape_result.jobs) if scrape_result.success else 0,
            new_jobs=len(new_job_ids),
            duration_ms=scrape_result.duration_ms,
            error_message=scrape_result.error,
            extra_data=extra_data,
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

    @staticmethod
    async def _extract_job_skills(db: AsyncSession, job: Job) -> int:
        """Populate job_skills from the job's title + description via the shared
        taxonomy. Idempotent (ON CONFLICT DO NOTHING on uq_job_skill). Returns count.

        All extracted skills are stored is_required=True — the keyword taxonomy
        can't tell required from nice-to-have; the weighting in the recommendation
        query is ready for that distinction once JD parsing provides it."""
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from app.core.skills import extract_skills
        from app.models.job_skill import JobSkill

        text = f"{job.title or ''}\n{job.description or ''}"
        skills = extract_skills(text)
        if not skills:
            return 0

        rows = [
            {
                "job_id": job.id,
                "skill_name": name,
                "skill_category": category,
                "is_required": True,
            }
            for name, category in skills
        ]
        await db.execute(
            pg_insert(JobSkill)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_job_skill")
        )
        return len(rows)

    @staticmethod
    def _structural_issues(scraped_job: ScrapedJob) -> List[str]:
        """Cheap, no-network sanity checks. A non-empty list => mark suspect.

        These catch scraper parser drift (a site redesign yielding empty titles,
        garbage URLs, etc.) before such jobs reach users."""
        issues: List[str] = []

        title = (scraped_job.title or "").strip()
        if not title:
            issues.append("empty_title")
        elif len(scraped_job.title) > 255:
            issues.append("title_too_long")

        desc = (scraped_job.description or "").strip()
        if len(desc) < 20:
            issues.append("short_description")

        parsed = urlparse(scraped_job.apply_url or "")
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            issues.append("bad_apply_url")

        if scraped_job.posted_at is not None:
            posted = scraped_job.posted_at
            if posted.tzinfo is None:
                posted = posted.replace(tzinfo=timezone.utc)
            if posted > datetime.now(timezone.utc):
                issues.append("future_posted_at")

        return issues

    async def get_due_sources(self, db: AsyncSession) -> List[str]:
        """
        Get IDs of sources that are due for scraping.

        Why return IDs instead of objects? Because the caller (Celery Beat)
        will send each ID as a separate task. The task then loads the source
        fresh from the DB - preventing stale data issues.
        """
        sources = await self.source_repo.get_due_sources(db)
        return [str(s.id) for s in sources]
