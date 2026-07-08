"""Forgot/reset password and email verification flows."""
from sqlalchemy import text

from app.core.database import engine

from .conftest import auth_headers


async def test_forgot_password_unknown_email_still_200(client, sent_emails):
    res = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "ghost@test.jobscout.com"}
    )
    assert res.status_code == 200
    assert sent_emails.items == []  # nothing sent, same response


async def test_forgot_then_reset_happy_path(client, registered_user, sent_emails, login):
    res = await client.post(
        "/api/v1/auth/forgot-password", json={"email": registered_user["email"]}
    )
    assert res.status_code == 200
    token = sent_emails.last_token()

    reset = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "FreshSecret9!"},
    )
    assert reset.status_code == 200

    old = await login(client, registered_user["email"], registered_user["password"])
    assert old.status_code == 401
    new = await login(client, registered_user["email"], "FreshSecret9!")
    assert new.status_code == 200


async def test_reset_revokes_all_sessions(client, registered_user, sent_emails):
    tokens = registered_user["tokens"]
    await client.post(
        "/api/v1/auth/forgot-password", json={"email": registered_user["email"]}
    )
    await client.post(
        "/api/v1/auth/reset-password",
        json={"token": sent_emails.last_token(), "new_password": "FreshSecret9!"},
    )

    refresh = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refresh.status_code == 401
    me = await client.get("/api/v1/users/me", headers=auth_headers(tokens))
    assert me.status_code == 401


async def test_reset_token_single_use(client, registered_user, sent_emails):
    await client.post(
        "/api/v1/auth/forgot-password", json={"email": registered_user["email"]}
    )
    token = sent_emails.last_token()

    first = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "FreshSecret9!"},
    )
    assert first.status_code == 200

    replay = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "AnotherSecret9!"},
    )
    assert replay.status_code == 401


async def test_reset_token_expired_401(client, registered_user, sent_emails):
    await client.post(
        "/api/v1/auth/forgot-password", json={"email": registered_user["email"]}
    )
    token = sent_emails.last_token()

    async with engine.begin() as db:
        await db.execute(
            text("UPDATE email_tokens SET expires_at = now() - interval '1 hour'")
        )

    res = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": "FreshSecret9!"},
    )
    assert res.status_code == 401


async def test_register_sends_verification_and_verify_sets_flag(client, sent_emails):
    res = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "verifyme@test.jobscout.com",
            "password": "VerifyMe123!",
            "full_name": "Verify Me",
        },
    )
    assert res.status_code == 201
    token = sent_emails.last_token()

    verify = await client.post("/api/v1/auth/verify-email", json={"token": token})
    assert verify.status_code == 200

    async with engine.begin() as db:
        flag = (
            await db.execute(
                text("SELECT email_verified FROM users WHERE email = 'verifyme@test.jobscout.com'")
            )
        ).scalar_one()
    assert flag is True


async def test_resend_verification_requires_auth(client):
    res = await client.post("/api/v1/auth/resend-verification")
    assert res.status_code == 401
