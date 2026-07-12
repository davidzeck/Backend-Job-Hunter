"""
Job-recommendation tests (roadmap Phase A #2): shared skill extraction, job-skill
population at ingest, the weighted skill-coverage ranking query + endpoint, and
skill-aware alert matching.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core.database import async_session_maker, engine
from app.core.skills import extract_skills
from app.models.company import Company
from app.models.job import Job
from app.models.job_skill import JobSkill
from app.models.job_source import JobSource
from app.models.user_skill import UserSkill
from app.repositories.job_repository import JobRepository
from app.services.job_service import JobService
from app.services.notification_service import NotificationService


# ── Factories ───────────────────────────────────────────────────────────

async def _make_company_source():
    async with async_session_maker() as db:
        company = Company(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}")
        db.add(company)
        await db.flush()
        source = JobSource(
            company_id=company.id, source_type="api",
            url="https://acme.example.com", scraper_class="greenhouse",
        )
        db.add(source)
        await db.commit()
        return company.id, source.id


async def _make_job(*, company_id, source_id, title="Backend Engineer",
                    skills=None, discovered_at=None, validation_status="valid"):
    async with async_session_maker() as db:
        job = Job(
            source_id=source_id, company_id=company_id,
            external_id=f"ext-{uuid.uuid4().hex[:8]}",
            title=title, apply_url="https://acme.example.com/1",
            discovered_at=discovered_at or datetime.now(timezone.utc),
            validation_status=validation_status,
        )
        db.add(job)
        await db.flush()
        for name in (skills or []):
            db.add(JobSkill(job_id=job.id, skill_name=name, is_required=True))
        await db.commit()
        return job.id


async def _add_user_skills(user_id, names):
    async with async_session_maker() as db:
        for name in names:
            db.add(UserSkill(user_id=user_id, skill_name=name, source="manual"))
        await db.commit()


async def _user_id(email):
    async with engine.begin() as db:
        r = await db.execute(text("SELECT id FROM users WHERE email=:e"), {"e": email})
        return r.scalar()


async def _set_prefs(email, prefs):
    import json
    async with engine.begin() as db:
        await db.execute(
            text("UPDATE users SET preferences = CAST(:p AS jsonb), fcm_token = 'tok' WHERE email = :e"),
            {"p": json.dumps(prefs), "e": email},
        )


# ── Shared extraction ───────────────────────────────────────────────────

def test_extract_skills_finds_taxonomy_terms():
    found = {name for name, _ in extract_skills("We build with Python, FastAPI and PostgreSQL")}
    assert {"Python", "FastAPI", "PostgreSQL"} <= found


# ── Job-skill population at ingest (via the service helper) ─────────────

async def test_extract_job_skills_populates_rows():
    from app.services.scrape_service import ScrapeService

    cid, sid = await _make_company_source()
    async with async_session_maker() as db:
        job = Job(
            source_id=sid, company_id=cid, external_id="e1",
            title="Senior Django Developer",
            description="Build REST APIs with Django and PostgreSQL. Docker a plus.",
            apply_url="https://acme.example.com/1",
            discovered_at=datetime.now(timezone.utc),
        )
        db.add(job)
        await db.flush()
        n = await ScrapeService._extract_job_skills(db, job)
        await db.commit()
        job_id = job.id
    assert n >= 3

    async with async_session_maker() as db:
        rows = await JobRepository().get_job_skills(db, job_id)
    names = {r.skill_name for r in rows}
    assert {"Django", "PostgreSQL", "Docker"} <= names


# ── Ranking query ───────────────────────────────────────────────────────

async def test_recommendation_ranking_and_coverage():
    cid, sid = await _make_company_source()
    # full overlap (2/2), partial (1/2), zero overlap (excluded)
    full = await _make_job(company_id=cid, source_id=sid, title="A", skills=["Python", "FastAPI"])
    partial = await _make_job(company_id=cid, source_id=sid, title="B", skills=["Python", "Kafka"])
    await _make_job(company_id=cid, source_id=sid, title="C", skills=["Rust", "Elixir"])

    repo = JobRepository()
    async with async_session_maker() as db:
        ranked, total = await repo.find_recommended(
            db, user_skill_names=["Python", "FastAPI"], page=1, limit=20
        )

    assert total == 2  # zero-overlap job excluded by HAVING
    ids = [r[0] for r in ranked]
    assert ids[0] == full and ids[1] == partial  # 1.0 ranks above 0.5
    assert ranked[0][1] == pytest.approx(1.0)
    assert ranked[1][1] == pytest.approx(0.5)
    assert set(ranked[0][2]) == {"Python", "FastAPI"}  # matched_skills


async def test_recommendation_empty_when_user_has_no_skills():
    repo = JobRepository()
    async with async_session_maker() as db:
        ranked, total = await repo.find_recommended(db, user_skill_names=[], page=1, limit=20)
    assert ranked == [] and total == 0


async def test_list_recommended_scales_score_to_100(registered_user):
    cid, sid = await _make_company_source()
    await _make_job(company_id=cid, source_id=sid, title="A", skills=["Python", "FastAPI"])
    uid = await _user_id(registered_user["email"])
    await _add_user_skills(uid, ["Python", "FastAPI"])

    async with async_session_maker() as db:
        page = await JobService().list_recommended(db, uid, page=1, limit=20)
    assert page.total == 1
    item = page.items[0]
    assert item.match_score == 100.0
    assert set(item.matched_skills) == {"Python", "FastAPI"}


# ── Skill-aware alerting ────────────────────────────────────────────────

async def _notify(job_id):
    async with async_session_maker() as db:
        return await NotificationService().notify_for_new_job(db, job_id)


async def _alert_count(job_id, user_id):
    async with engine.begin() as db:
        r = await db.execute(
            text("SELECT count(*) FROM user_job_alerts WHERE job_id=:j AND user_id=:u"),
            {"j": job_id, "u": user_id},
        )
        return r.scalar()


async def test_skill_match_alerts_when_prefs_dont(registered_user):
    """A user whose CV skills cover the job — but whose role/location prefs do
    NOT match — still gets alerted when skill_alerts_enabled."""
    cid, sid = await _make_company_source()
    job_id = await _make_job(company_id=cid, source_id=sid, skills=["Python", "FastAPI"])
    uid = await _user_id(registered_user["email"])
    await _add_user_skills(uid, ["Python", "FastAPI"])
    await _set_prefs(registered_user["email"], {
        "roles": ["ux_designer"], "locations": ["antarctica"], "companies": [],
        "skill_alerts_enabled": True, "notifications": {"push": True},
    })

    result = await _notify(job_id)
    assert result["matching_users"] == 1
    assert await _alert_count(job_id, uid) == 1


async def test_skill_alerts_disabled_suppresses(registered_user):
    cid, sid = await _make_company_source()
    job_id = await _make_job(company_id=cid, source_id=sid, skills=["Python", "FastAPI"])
    uid = await _user_id(registered_user["email"])
    await _add_user_skills(uid, ["Python", "FastAPI"])
    await _set_prefs(registered_user["email"], {
        "roles": ["ux_designer"], "locations": ["antarctica"], "companies": [],
        "skill_alerts_enabled": False, "notifications": {"push": True},
    })

    result = await _notify(job_id)
    assert result["matching_users"] == 0
    assert await _alert_count(job_id, uid) == 0


async def test_preference_match_still_works_without_skills(registered_user):
    """Regression: preference-based matching is unaffected by the skill path."""
    cid, sid = await _make_company_source()
    job_id = await _make_job(
        company_id=cid, source_id=sid, title="Backend Engineer", skills=["Python"]
    )
    uid = await _user_id(registered_user["email"])
    # No user skills; role preference matches the title.
    await _set_prefs(registered_user["email"], {
        "roles": ["backend"], "locations": [], "companies": [],
        "skill_alerts_enabled": True, "notifications": {"push": True},
    })

    result = await _notify(job_id)
    assert result["matching_users"] == 1
    assert await _alert_count(job_id, uid) == 1
