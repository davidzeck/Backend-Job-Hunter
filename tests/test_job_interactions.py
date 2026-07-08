"""Browse-view Save/Applied interactions on jobs."""
import uuid

import pytest
from sqlalchemy import text

from app.core.database import engine
from .conftest import auth_headers


@pytest.fixture
async def job(_clean_state):
    """Insert a company + source + one active job; return its id as a string."""
    company_id = uuid.uuid4()
    source_id = uuid.uuid4()
    job_id = uuid.uuid4()
    async with engine.begin() as db:
        await db.execute(
            text(
                "INSERT INTO companies (id, name, slug, is_active, created_at, updated_at) "
                "VALUES (:id, 'Acme', :slug, true, now(), now())"
            ),
            {"id": company_id, "slug": f"acme-{uuid.uuid4().hex[:8]}"},
        )
        await db.execute(
            text(
                "INSERT INTO job_sources (id, company_id, source_type, url, scraper_class, "
                "scrape_interval_minutes, is_active, health_status, consecutive_failures, "
                "config, created_at, updated_at) "
                "VALUES (:id, :cid, 'ats_api', 'https://x/y', 'greenhouse', 60, true, "
                "'healthy', 0, '{}', now(), now())"
            ),
            {"id": source_id, "cid": company_id},
        )
        await db.execute(
            text(
                "INSERT INTO jobs (id, source_id, company_id, title, apply_url, "
                "discovered_at, is_active, created_at, updated_at) "
                "VALUES (:id, :sid, :cid, 'Backend Engineer', 'https://acme/apply', "
                "now(), true, now(), now())"
            ),
            {"id": job_id, "sid": source_id, "cid": company_id},
        )
    return str(job_id)


async def test_save_toggle_persists(client, registered_user, job):
    h = auth_headers(registered_user["tokens"])

    # Initially not saved in the list
    lst = await client.get("/api/v1/jobs/?days_ago=30", headers=h)
    item = next(i for i in lst.json()["items"] if i["id"] == job)
    assert item["saved"] is False and item["applied"] is False

    # Save it
    res = await client.put(f"/api/v1/jobs/{job}/saved", json={"saved": True}, headers=h)
    assert res.status_code == 200, res.text
    assert res.json()["saved"] is True

    # Detail reflects it
    detail = await client.get(f"/api/v1/jobs/{job}", headers=h)
    assert detail.json()["saved"] is True

    # Saved list contains it
    saved = await client.get("/api/v1/jobs/saved", headers=h)
    assert any(i["id"] == job for i in saved.json()["items"])

    # Unsave
    res = await client.put(f"/api/v1/jobs/{job}/saved", json={"saved": False}, headers=h)
    assert res.json()["saved"] is False
    saved = await client.get("/api/v1/jobs/saved", headers=h)
    assert all(i["id"] != job for i in saved.json()["items"])


async def test_applied_toggle_persists(client, registered_user, job):
    h = auth_headers(registered_user["tokens"])

    res = await client.put(f"/api/v1/jobs/{job}/applied", json={"applied": True}, headers=h)
    assert res.status_code == 200, res.text
    assert res.json()["applied"] is True

    detail = await client.get(f"/api/v1/jobs/{job}", headers=h)
    assert detail.json()["applied"] is True


async def test_interactions_are_per_user(client, registered_user, admin_user, job):
    # User A saves
    await client.put(
        f"/api/v1/jobs/{job}/saved", json={"saved": True},
        headers=auth_headers(registered_user["tokens"]),
    )
    # User B does not see it as saved
    detail_b = await client.get(
        f"/api/v1/jobs/{job}", headers=auth_headers(admin_user["tokens"])
    )
    assert detail_b.json()["saved"] is False


async def test_save_unknown_job_404(client, registered_user):
    res = await client.put(
        f"/api/v1/jobs/{uuid.uuid4()}/saved", json={"saved": True},
        headers=auth_headers(registered_user["tokens"]),
    )
    assert res.status_code == 404


async def test_interactions_require_auth(client, job):
    res = await client.put(f"/api/v1/jobs/{job}/saved", json={"saved": True})
    assert res.status_code == 401
