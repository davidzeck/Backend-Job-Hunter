"""Admin RBAC: sources fully admin; company writes admin; stats admin."""
from .conftest import auth_headers


async def test_sources_list_requires_admin(client, registered_user, admin_user):
    user_res = await client.get(
        "/api/v1/sources/", headers=auth_headers(registered_user["tokens"])
    )
    assert user_res.status_code == 403

    admin_res = await client.get(
        "/api/v1/sources/", headers=auth_headers(admin_user["tokens"])
    )
    assert admin_res.status_code == 200


async def test_companies_create_403_for_user_201_for_admin(
    client, registered_user, admin_user
):
    payload = {"name": "RBAC Test Co", "slug": "rbac-test-co"}

    user_res = await client.post(
        "/api/v1/companies/", json=payload, headers=auth_headers(registered_user["tokens"])
    )
    assert user_res.status_code == 403

    admin_res = await client.post(
        "/api/v1/companies/", json=payload, headers=auth_headers(admin_user["tokens"])
    )
    assert admin_res.status_code == 201


async def test_companies_list_ok_for_normal_user(client, registered_user):
    res = await client.get(
        "/api/v1/companies/", headers=auth_headers(registered_user["tokens"])
    )
    assert res.status_code == 200


async def test_dashboard_stats_admin_only(client, registered_user, admin_user):
    user_res = await client.get(
        "/api/v1/dashboard/stats", headers=auth_headers(registered_user["tokens"])
    )
    assert user_res.status_code == 403

    admin_res = await client.get(
        "/api/v1/dashboard/stats", headers=auth_headers(admin_user["tokens"])
    )
    assert admin_res.status_code == 200


async def test_jobs_remain_user_accessible(client, registered_user):
    res = await client.get(
        "/api/v1/jobs/", headers=auth_headers(registered_user["tokens"])
    )
    assert res.status_code == 200


async def test_profile_reports_is_admin(client, registered_user, admin_user):
    """GET /users/me must surface is_admin — the dashboard gates nav on it."""
    user_me = await client.get(
        "/api/v1/users/me", headers=auth_headers(registered_user["tokens"])
    )
    assert user_me.json()["is_admin"] is False

    admin_me = await client.get(
        "/api/v1/users/me", headers=auth_headers(admin_user["tokens"])
    )
    assert admin_me.json()["is_admin"] is True
