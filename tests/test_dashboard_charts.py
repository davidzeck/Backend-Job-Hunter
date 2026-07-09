"""Dashboard chart endpoints: admin-only, correct shape, empty-safe."""
from .conftest import auth_headers


async def test_jobs_timeline_shape_and_admin_only(client, registered_user, admin_user):
    # Non-admin forbidden
    r = await client.get(
        "/api/v1/dashboard/jobs-timeline?days=7",
        headers=auth_headers(registered_user["tokens"]),
    )
    assert r.status_code == 403

    r = await client.get(
        "/api/v1/dashboard/jobs-timeline?days=7",
        headers=auth_headers(admin_user["tokens"]),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data) == 7  # zero-filled even with no jobs
    assert set(data[0].keys()) == {"date", "jobs", "new_jobs"}


async def test_scrape_activity_shape(client, admin_user):
    r = await client.get(
        "/api/v1/dashboard/scrape-activity?hours=24",
        headers=auth_headers(admin_user["tokens"]),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data) == 24
    assert set(data[0].keys()) == {"hour", "scrapes", "success", "failed"}


async def test_source_performance_shape(client, admin_user):
    r = await client.get(
        "/api/v1/dashboard/source-performance",
        headers=auth_headers(admin_user["tokens"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["data"].keys()) == {"active", "error", "paused", "inactive"}
    assert "success_rate" in body


async def test_activity_empty_ok_and_admin_only(client, registered_user, admin_user):
    r = await client.get(
        "/api/v1/dashboard/activity",
        headers=auth_headers(registered_user["tokens"]),
    )
    assert r.status_code == 403

    r = await client.get(
        "/api/v1/dashboard/activity",
        headers=auth_headers(admin_user["tokens"]),
    )
    assert r.status_code == 200, r.text
    assert r.json() == []  # no scrape logs in a clean test DB
