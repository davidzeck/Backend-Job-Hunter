"""
Job validation service — verifies scraped jobs before they reach users.

Runs between ingest and alert fan-out (see `validate_job` in tasks.py) and in
the nightly staleness sweep (scheduler.py). The product sells speed-to-apply, so
a dead or misattributed apply link is worse than no alert — but a flaky network
check must never silently suppress a real job. Hence the outcome model below is
deliberately conservative: only a *definitive* signal marks a job dead/suspect;
anything ambiguous stays `unverified` and is allowed through.

Structural sanity (title/description/url shape) runs synchronously at ingest in
scrape_service — it needs no network and catches scraper parser drift. This
service owns the network checks: apply-URL liveness + company-domain match.
"""
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.repositories.job_repository import JobRepository
from app.scrapers.base import BaseScraper

logger = get_logger(__name__)

# Validation status values (mirror on Job.validation_status).
VALID = "valid"
UNVERIFIED = "unverified"
SUSPECT = "suspect"
DEAD = "dead"

# Known ATS / application-portal hosts. A job whose apply URL lands on one of
# these is legitimate even when the host differs from the company domain
# (companies use hosted ATSes). Suffix-matched, so subdomains count.
KNOWN_ATS_DOMAINS = (
    "greenhouse.io",
    "boards.greenhouse.io",
    "lever.co",
    "jobs.lever.co",
    "myworkdayjobs.com",
    "workable.com",
    "smartrecruiters.com",
    "ashbyhq.com",
    "bamboohr.com",
    "breezy.hr",
    "recruitee.com",
    "teamtailor.com",
    "remotive.com",
)


# Leading seniority words stripped before comparing titles for cross-source dedup.
_SENIORITY_PREFIXES = (
    "senior", "sr", "junior", "jr", "lead", "principal", "staff", "mid",
    "entry level", "entry", "associate",
)


def normalize_title(title: Optional[str]) -> str:
    """Lowercase, strip punctuation + a leading seniority word, collapse spaces.

    Used for cross-source duplicate detection: "Sr. Backend Engineer" and
    "Backend Engineer" from two sources normalize to the same string.
    """
    t = re.sub(r"[^a-z0-9 ]+", " ", (title or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    for prefix in _SENIORITY_PREFIXES:
        if t.startswith(prefix + " "):
            return t[len(prefix) + 1:].strip()
    return t


@dataclass
class ValidationResult:
    status: str                      # VALID | UNVERIFIED | SUSPECT | DEAD
    detail: dict = field(default_factory=dict)


def _registered_domain(host: str) -> str:
    """Naive eTLD+1: last two labels. Good enough for host-family comparison."""
    host = (host or "").lower().strip().rstrip(".")
    parts = [p for p in host.split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _host_of(url: Optional[str]) -> str:
    if not url:
        return ""
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _is_known_ats(host: str) -> bool:
    reg = _registered_domain(host)
    return any(host == d or host.endswith("." + d) or reg == d for d in KNOWN_ATS_DOMAINS)


class ValidationService:
    """Network validation for a single job's apply URL."""

    def __init__(self):
        self.job_repo = JobRepository()

    async def validate_job(self, db: AsyncSession, job_id: UUID) -> bool:
        """
        Validate one job (called from the `validate_job` task, between ingest and
        alert fan-out). Persists status/detail. Returns True if the job should
        proceed to alerting — i.e. status is `valid` or `unverified` (fail-open).
        """
        job = await self.job_repo.get_with_company(db, job_id)
        if not job:
            return False

        company_domain = _host_of(job.company.careers_url) if job.company else ""
        result = await self.check_apply_url(
            job.apply_url, company_domain=company_domain or None
        )
        job.validation_status = result.status
        job.last_validated_at = datetime.now(timezone.utc)
        job.validation_detail = result.detail
        await db.commit()

        should_alert = result.status in (VALID, UNVERIFIED)
        logger.info(
            "job_validated",
            job_id=str(job_id),
            status=result.status,
            should_alert=should_alert,
        )
        return should_alert

    async def revalidate_stale(self, db: AsyncSession) -> dict:
        """
        Nightly staleness sweep: re-check liveness for the oldest-validated active
        jobs. Only liveness matters here (no domain check). A job that reads DEAD
        twice in a row is deactivated; a single dead reading just flags it, so a
        transient outage doesn't wrongly kill a live listing.
        """
        jobs = await self.job_repo.get_stale_active_jobs(
            db,
            older_than_days=settings.revalidate_after_days,
            limit=settings.revalidate_batch_size,
        )
        checked = 0
        deactivated = 0
        for job in jobs:
            result = await self.check_apply_url(job.apply_url)  # liveness only
            checked += 1
            detail = dict(job.validation_detail or {})

            if result.status == DEAD:
                streak = int(detail.get("dead_streak", 0)) + 1
                detail["dead_streak"] = streak
                if streak >= 2:
                    job.is_active = False
                    job.validation_status = DEAD
                    deactivated += 1
                else:
                    job.validation_status = SUSPECT  # first strike, keep it alive
            else:
                detail.pop("dead_streak", None)  # recovered / reachable

            detail["last_http_status"] = result.detail.get("http_status")
            job.validation_detail = detail
            job.last_validated_at = datetime.now(timezone.utc)

        await db.commit()
        logger.info("revalidate_stale_done", checked=checked, deactivated=deactivated)
        return {"checked": checked, "deactivated": deactivated}

    async def check_apply_url(
        self,
        apply_url: str,
        *,
        company_domain: Optional[str] = None,
    ) -> ValidationResult:
        """
        Liveness + domain cross-check on an apply URL. Never raises.

        Outcomes:
          DEAD       — 404/410 (listing gone)
          SUSPECT    — reachable but final host neither matches the company
                       domain nor a known ATS (possible misattribution)
          UNVERIFIED — timeout / connection error / 5xx (ambiguous; allow through)
          VALID      — reachable and host checks out
        """
        detail: dict = {"apply_url": apply_url}
        headers = {"User-Agent": random.choice(BaseScraper.USER_AGENTS)}

        try:
            async with httpx.AsyncClient(
                timeout=settings.validation_timeout_seconds,
                follow_redirects=True,
                headers=headers,
            ) as client:
                resp = await self._head_or_get(client, apply_url)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            detail["error"] = type(exc).__name__
            logger.info("validation_unverified_network", **detail)
            return ValidationResult(UNVERIFIED, detail)

        status_code = resp.status_code
        final_host = _host_of(str(resp.url))
        detail["http_status"] = status_code
        detail["final_host"] = final_host

        if status_code in (404, 410):
            return ValidationResult(DEAD, detail)
        if status_code >= 500:
            # Server-side hiccup — ambiguous, don't punish the job.
            return ValidationResult(UNVERIFIED, detail)

        # Reachable (2xx/3xx/4xx-other). Now the domain cross-check.
        domain_ok = _is_known_ats(final_host)
        if not domain_ok and company_domain:
            domain_ok = _registered_domain(final_host) == _registered_domain(company_domain)
        detail["domain_ok"] = domain_ok

        if not domain_ok:
            return ValidationResult(SUSPECT, detail)
        return ValidationResult(VALID, detail)

    async def _head_or_get(
        self, client: httpx.AsyncClient, url: str
    ) -> httpx.Response:
        """HEAD first; fall back to a capped GET when HEAD is rejected/unsupported."""
        resp = await client.head(url)
        if resp.status_code in (405, 501) or (resp.status_code == 403):
            # Some ATSes reject/limit HEAD — retry with GET, reading little.
            resp = await client.get(url)
        return resp
