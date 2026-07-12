"""
Job-validation tests (roadmap Phase A #3): the validation service (URL liveness,
domain cross-check, title normalization), the ingest-time structural + dedup
gates, the validate/alert decision, the nightly staleness sweep, and the
dead-exclusion query. Network is mocked with httpx.MockTransport.
"""
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.core.database import async_session_maker
from app.repositories.job_repository import JobRepository
from app.models.company import Company
from app.models.job import Job
from app.models.job_source import JobSource
from app.scrapers.base import ScrapedJob
from app.services import validation_service as vs
from app.services.scrape_service import ScrapeService
from app.services.validation_service import (
    DEAD,
    SUSPECT,
    UNVERIFIED,
    VALID,
    ValidationResult,
    ValidationService,
    normalize_title,
)


# ── Factories ───────────────────────────────────────────────────────────

async def _make_company_source(careers_url: str | None = None):
    async with async_session_maker() as db:
        company = Company(
            name="Acme",
            slug=f"acme-{uuid.uuid4().hex[:6]}",
            careers_url=careers_url,
        )
        db.add(company)
        await db.flush()
        source = JobSource(
            company_id=company.id,
            source_type="api",
            url="https://acme.example.com",
            scraper_class="greenhouse",
        )
        db.add(source)
        await db.commit()
        return company.id, source.id


async def _make_job(
    *,
    company_id,
    source_id,
    title="Backend Engineer",
    apply_url="https://acme.example.com/apply/1",
    external_id=None,
    discovered_at=None,
    validation_status="unverified",
    last_validated_at=None,
):
    async with async_session_maker() as db:
        job = Job(
            source_id=source_id,
            company_id=company_id,
            external_id=external_id or f"ext-{uuid.uuid4().hex[:8]}",
            title=title,
            apply_url=apply_url,
            discovered_at=discovered_at or datetime.now(timezone.utc),
            validation_status=validation_status,
            last_validated_at=last_validated_at,
        )
        db.add(job)
        await db.commit()
        return job.id


def _mock_httpx(monkeypatch, handler):
    """Route all httpx.AsyncClient traffic through a MockTransport handler."""
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(vs.httpx, "AsyncClient", factory)


# ── normalize_title ─────────────────────────────────────────────────────

def test_normalize_title_strips_seniority_and_punctuation():
    assert normalize_title("Sr. Backend Engineer") == "backend engineer"
    assert normalize_title("Senior Backend Engineer") == "backend engineer"
    assert normalize_title("Backend Engineer") == "backend engineer"
    assert normalize_title("Lead  Data  Scientist!") == "data scientist"
    # non-seniority leading words are preserved
    assert normalize_title("Cloud Engineer") == "cloud engineer"


# ── check_apply_url outcome mapping ─────────────────────────────────────

async def test_liveness_404_is_dead(monkeypatch):
    _mock_httpx(monkeypatch, lambda req: httpx.Response(404))
    result = await ValidationService().check_apply_url("https://acme.example.com/x")
    assert result.status == DEAD


async def test_known_ats_host_is_valid(monkeypatch):
    _mock_httpx(monkeypatch, lambda req: httpx.Response(200))
    result = await ValidationService().check_apply_url("https://boards.greenhouse.io/acme/jobs/1")
    assert result.status == VALID
    assert result.detail["domain_ok"] is True


async def test_matching_company_domain_is_valid(monkeypatch):
    _mock_httpx(monkeypatch, lambda req: httpx.Response(200))
    result = await ValidationService().check_apply_url(
        "https://careers.acme.com/1", company_domain="acme.com"
    )
    assert result.status == VALID


async def test_mismatched_domain_is_suspect(monkeypatch):
    _mock_httpx(monkeypatch, lambda req: httpx.Response(200))
    result = await ValidationService().check_apply_url(
        "https://totally-different.xyz/1", company_domain="acme.com"
    )
    assert result.status == SUSPECT


async def test_5xx_is_unverified(monkeypatch):
    _mock_httpx(monkeypatch, lambda req: httpx.Response(503))
    result = await ValidationService().check_apply_url("https://acme.example.com/x")
    assert result.status == UNVERIFIED


async def test_timeout_is_unverified(monkeypatch):
    def _boom(req):
        raise httpx.ConnectTimeout("slow", request=req)

    _mock_httpx(monkeypatch, _boom)
    result = await ValidationService().check_apply_url("https://acme.example.com/x")
    assert result.status == UNVERIFIED


async def test_head_405_falls_back_to_get(monkeypatch):
    def handler(req):
        return httpx.Response(405) if req.method == "HEAD" else httpx.Response(200)

    _mock_httpx(monkeypatch, handler)
    result = await ValidationService().check_apply_url("https://boards.greenhouse.io/acme/1")
    assert result.status == VALID


# ── validate_job: persistence + alert decision (fail-open) ──────────────

async def test_validate_job_dead_persists_and_suppresses(monkeypatch):
    cid, sid = await _make_company_source()
    job_id = await _make_job(company_id=cid, source_id=sid)

    async def fake(self, apply_url, *, company_domain=None):
        return ValidationResult(DEAD, {"http_status": 404})

    monkeypatch.setattr(ValidationService, "check_apply_url", fake)

    async with async_session_maker() as db:
        should_alert = await ValidationService().validate_job(db, job_id)
    assert should_alert is False

    repo = JobRepository()
    async with async_session_maker() as db:
        job = await repo.get_by_id(db, job_id)
        assert job.validation_status == DEAD
        assert job.last_validated_at is not None


async def test_validate_job_unverified_still_alerts(monkeypatch):
    """Fail-open: a network hiccup must NOT drop a real job."""
    cid, sid = await _make_company_source()
    job_id = await _make_job(company_id=cid, source_id=sid)

    async def fake(self, apply_url, *, company_domain=None):
        return ValidationResult(UNVERIFIED, {"error": "ConnectTimeout"})

    monkeypatch.setattr(ValidationService, "check_apply_url", fake)

    async with async_session_maker() as db:
        should_alert = await ValidationService().validate_job(db, job_id)
    assert should_alert is True


# ── Structural checks (ingest, no network) ──────────────────────────────

def test_structural_issues_flags_bad_jobs():
    good = ScrapedJob(
        external_id="1",
        title="Backend Engineer",
        apply_url="https://acme.example.com/1",
        description="We are hiring a backend engineer to build APIs.",
    )
    assert ScrapeService._structural_issues(good) == []

    bad = ScrapedJob(
        external_id="2",
        title="",
        apply_url="not-a-url",
        description="short",
    )
    issues = ScrapeService._structural_issues(bad)
    assert "empty_title" in issues
    assert "bad_apply_url" in issues
    assert "short_description" in issues


def test_structural_issues_future_posted_at():
    job = ScrapedJob(
        external_id="3",
        title="Data Scientist",
        apply_url="https://acme.example.com/3",
        description="A perfectly reasonable description of the role here.",
        posted_at=datetime.now(timezone.utc) + timedelta(days=5),
    )
    assert "future_posted_at" in ScrapeService._structural_issues(job)


# ── Cross-source dedup query ────────────────────────────────────────────

async def test_cross_source_duplicate_detected():
    cid, sid1 = await _make_company_source()
    # second source, same company
    async with async_session_maker() as db:
        source2 = JobSource(
            company_id=cid, source_type="api",
            url="https://other.example.com", scraper_class="lever",
        )
        db.add(source2)
        await db.commit()
        sid2 = source2.id

    await _make_job(company_id=cid, source_id=sid1, title="Backend Engineer")
    dup_id = await _make_job(company_id=cid, source_id=sid2, title="Senior Backend Engineer")

    repo = JobRepository()
    async with async_session_maker() as db:
        match = await repo.find_cross_source_duplicate(
            db,
            company_id=cid,
            source_id=sid2,
            title="Senior Backend Engineer",
            exclude_job_id=dup_id,
        )
    assert match is not None  # normalizes to the same "backend engineer"


# ── Dead exclusion / admin filter ───────────────────────────────────────

async def test_list_excludes_dead_by_default():
    cid, sid = await _make_company_source()
    await _make_job(company_id=cid, source_id=sid, validation_status=VALID)
    await _make_job(company_id=cid, source_id=sid, validation_status=DEAD)
    await _make_job(company_id=cid, source_id=sid, validation_status=SUSPECT)

    repo = JobRepository()
    async with async_session_maker() as db:
        jobs, total = await repo.find_with_filters(db, days_ago=30)
        assert total == 2  # dead excluded
        statuses = {j.validation_status for j in jobs}
        assert DEAD not in statuses

        suspect, s_total = await repo.find_with_filters(
            db, days_ago=30, validation_status=SUSPECT
        )
        assert s_total == 1


# ── Nightly staleness sweep: two strikes → deactivate ───────────────────

async def test_revalidate_two_dead_strikes_deactivates(monkeypatch):
    cid, sid = await _make_company_source()
    old = datetime.now(timezone.utc) - timedelta(days=30)
    job_id = await _make_job(
        company_id=cid, source_id=sid, discovered_at=old, validation_status=VALID
    )

    async def fake(self, apply_url, *, company_domain=None):
        return ValidationResult(DEAD, {"http_status": 404})

    monkeypatch.setattr(ValidationService, "check_apply_url", fake)
    repo = JobRepository()

    # First sweep: flagged suspect, still active, one strike
    async with async_session_maker() as db:
        r1 = await ValidationService().revalidate_stale(db)
    assert r1["checked"] == 1 and r1["deactivated"] == 0
    async with async_session_maker() as db:
        job = await repo.get_by_id(db, job_id)
        assert job.is_active is True
        assert job.validation_status == SUSPECT
        assert job.validation_detail["dead_streak"] == 1

    # Second sweep: second strike → deactivated + dead
    async with async_session_maker() as db:
        r2 = await ValidationService().revalidate_stale(db)
    assert r2["deactivated"] == 1
    async with async_session_maker() as db:
        job = await repo.get_by_id(db, job_id)
        assert job.is_active is False
        assert job.validation_status == DEAD
