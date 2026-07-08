"""Logout revocation and the Redis access-token denylist."""
from datetime import datetime, timedelta, timezone

from jose import jwt

from app.core.config import settings

from .conftest import auth_headers


async def test_logout_revokes_refresh_session(client, registered_user):
    tokens = registered_user["tokens"]
    res = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    assert res.status_code == 200

    refresh = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refresh.status_code == 401


async def test_access_token_rejected_after_logout(client, registered_user):
    tokens = registered_user["tokens"]
    before = await client.get("/api/v1/users/me", headers=auth_headers(tokens))
    assert before.status_code == 200

    await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
        headers=auth_headers(tokens),
    )

    after = await client.get("/api/v1/users/me", headers=auth_headers(tokens))
    assert after.status_code == 401


async def test_logout_clears_web_cookie(client, registered_user, login):
    res = await login(
        client, registered_user["email"], registered_user["password"], web=True
    )
    assert client.cookies.get(settings.refresh_cookie_name)

    out = await client.post(
        "/api/v1/auth/logout", headers={"X-Client": "web"}, json={}
    )
    assert out.status_code == 200
    assert 'jobscout_refresh=""' in out.headers.get("set-cookie", "") or (
        client.cookies.get(settings.refresh_cookie_name) in (None, "")
    )


async def test_logout_without_tokens_still_200(client):
    res = await client.post("/api/v1/auth/logout", json={})
    assert res.status_code == 200


async def test_legacy_token_without_jti_rejected(client, registered_user):
    """Pre-hardening access tokens (no jti/sid) must be rejected."""
    now = datetime.now(timezone.utc)
    legacy = jwt.encode(
        {"sub": "some-user-id", "exp": now + timedelta(minutes=30), "type": "access"},
        settings.secret_key,
        algorithm=settings.algorithm,
    )
    res = await client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {legacy}"}
    )
    assert res.status_code == 401
