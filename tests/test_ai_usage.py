"""AI usage endpoint: quota snapshot the dashboard banner reads."""
from .conftest import auth_headers


async def test_ai_usage_requires_auth(client):
    res = await client.get("/api/v1/users/me/ai-usage")
    assert res.status_code == 401


async def test_ai_usage_fresh_user_full_quota(client, registered_user):
    res = await client.get(
        "/api/v1/users/me/ai-usage", headers=auth_headers(registered_user["tokens"])
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["used"] == 0
    assert body["limit"] == 50
    assert body["remaining"] == 50
    assert body["warn"] is False
    assert body["exhausted"] is False
    assert body["resets_in_seconds"] is None
