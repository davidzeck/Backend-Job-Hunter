"""Login/register contract tests — form-encoded OAuth2 flow + web cookie mode."""
import uuid

from sqlalchemy import text

from app.core.config import settings
from app.core.database import engine

from .conftest import auth_headers


async def test_register_returns_tokens_and_creates_session(client, registered_user):
    tokens = registered_user["tokens"]
    assert tokens["access_token"] and tokens["refresh_token"]
    assert tokens["token_type"] == "bearer"
    assert tokens["expires_in"] == settings.access_token_expire_minutes * 60

    async with engine.begin() as db:
        count = (
            await db.execute(text("SELECT count(*) FROM auth_sessions"))
        ).scalar_one()
    assert count == 1

    me = await client.get("/api/v1/users/me", headers=auth_headers(tokens))
    assert me.status_code == 200
    assert me.json()["email"] == registered_user["email"]


async def test_register_duplicate_email_409(client, registered_user):
    res = await client.post(
        "/api/v1/auth/register",
        json={
            "email": registered_user["email"],
            "password": "AnotherPass123!",
            "full_name": "Dup",
        },
    )
    assert res.status_code == 409


async def test_login_form_success(client, registered_user, login):
    res = await login(client, registered_user["email"], registered_user["password"])
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["access_token"] and body["refresh_token"]


async def test_login_wrong_password_401(client, registered_user, login):
    res = await login(client, registered_user["email"], "wrong-password-123")
    assert res.status_code == 401


async def test_login_json_body_422_contract_guard(client, registered_user):
    """Login is form-encoded by contract; a JSON body must be rejected."""
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": registered_user["email"], "password": registered_user["password"]},
    )
    assert res.status_code == 422


async def test_login_web_header_sets_cookie_and_omits_refresh_from_body(
    client, registered_user, login
):
    res = await login(client, registered_user["email"], registered_user["password"], web=True)
    assert res.status_code == 200
    assert res.json()["refresh_token"] is None

    set_cookie = res.headers.get("set-cookie", "")
    assert settings.refresh_cookie_name in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie or "samesite=lax" in set_cookie.lower()
    # Session cookie (no remember_me): no Max-Age
    assert "Max-Age" not in set_cookie


async def test_login_remember_me_sets_persistent_cookie_max_age(
    client, registered_user, login
):
    res = await login(
        client, registered_user["email"], registered_user["password"], web=True, remember=True
    )
    set_cookie = res.headers.get("set-cookie", "")
    assert f"Max-Age={settings.refresh_token_expire_days * 86400}" in set_cookie


async def test_login_rate_limit_429_after_5(client, login):
    email = f"nobody-{uuid.uuid4().hex[:8]}@test.jobscout.com"
    statuses = []
    for _ in range(6):
        res = await login(client, email, "whatever-pass")
        statuses.append(res.status_code)
    assert statuses[:5] == [401] * 5
    assert statuses[5] == 429
