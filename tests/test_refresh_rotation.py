"""Refresh rotation, reuse detection, and the session absolute-age cap."""
import uuid
from datetime import datetime, timedelta, timezone

from jose import jwt
from sqlalchemy import text

from app.core.config import settings
from app.core.database import engine

from .conftest import auth_headers


async def _refresh(client, token: str):
    return await client.post("/api/v1/auth/refresh", json={"refresh_token": token})


async def test_refresh_body_rotates_pair(client, registered_user):
    old = registered_user["tokens"]
    res = await _refresh(client, old["refresh_token"])
    assert res.status_code == 200, res.text
    new = res.json()
    assert new["refresh_token"] != old["refresh_token"]
    assert new["access_token"] != old["access_token"]

    # Two session rows now: origin (replaced) + child (active tail)
    async with engine.begin() as db:
        rows = (
            await db.execute(
                text("SELECT replaced_by IS NOT NULL AS replaced FROM auth_sessions ORDER BY created_at")
            )
        ).all()
    assert [r.replaced for r in rows] == [True, False]

    # New access token works
    me = await client.get("/api/v1/users/me", headers=auth_headers(new))
    assert me.status_code == 200


async def test_refresh_cookie_mode_rotates_cookie(client, registered_user, login):
    res = await login(
        client, registered_user["email"], registered_user["password"], web=True, remember=True
    )
    assert res.status_code == 200
    cookie_before = client.cookies.get(settings.refresh_cookie_name)
    assert cookie_before

    res2 = await client.post(
        "/api/v1/auth/refresh", headers={"X-Client": "web"}, json={}
    )
    assert res2.status_code == 200, res2.text
    assert res2.json()["refresh_token"] is None
    cookie_after = client.cookies.get(settings.refresh_cookie_name)
    assert cookie_after and cookie_after != cookie_before


async def test_reuse_within_grace_forks_instead_of_revoking(client, registered_user):
    old = registered_user["tokens"]["refresh_token"]
    first = await _refresh(client, old)
    assert first.status_code == 200

    # Immediately replay the old token (multi-tab race) → fork, not revocation
    second = await _refresh(client, old)
    assert second.status_code == 200
    # And the family is still alive
    third = await _refresh(client, second.json()["refresh_token"])
    assert third.status_code == 200


async def test_reuse_after_grace_revokes_family(client, registered_user):
    old = registered_user["tokens"]["refresh_token"]
    first = await _refresh(client, old)
    assert first.status_code == 200
    rotated = first.json()

    # Age the rotation past the grace window
    async with engine.begin() as db:
        await db.execute(
            text(
                "UPDATE auth_sessions SET last_used_at = now() - interval '10 minutes' "
                "WHERE replaced_by IS NOT NULL"
            )
        )

    replay = await _refresh(client, old)
    assert replay.status_code == 401

    # The WHOLE family is dead — the legitimately rotated token dies too
    after = await _refresh(client, rotated["refresh_token"])
    assert after.status_code == 401

    # And its access token is killed via the Redis sid marker
    me = await client.get("/api/v1/users/me", headers=auth_headers(rotated))
    assert me.status_code == 401


async def test_refresh_with_access_token_401(client, registered_user):
    res = await _refresh(client, registered_user["tokens"]["access_token"])
    assert res.status_code == 401


async def test_refresh_garbage_token_401(client):
    res = await _refresh(client, "not-a-jwt")
    assert res.status_code == 401


async def test_family_absolute_age_cap_revokes(client, registered_user):
    # Age the family origin beyond the absolute cap
    async with engine.begin() as db:
        await db.execute(
            text(
                "UPDATE auth_sessions SET created_at = now() - interval '31 days' "
                "WHERE family_id = id"
            )
        )
    res = await _refresh(client, registered_user["tokens"]["refresh_token"])
    assert res.status_code == 401


async def test_forged_refresh_with_unknown_jti_401(client, registered_user):
    """A validly-signed refresh whose jti has no session row must fail."""
    fake_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    forged = jwt.encode(
        {
            "sub": fake_id,
            "sid": fake_id,
            "jti": fake_id,
            "iat": now,
            "exp": now + timedelta(days=1),
            "type": "refresh",
        },
        settings.secret_key,
        algorithm=settings.algorithm,
    )
    res = await _refresh(client, forged)
    assert res.status_code == 401
