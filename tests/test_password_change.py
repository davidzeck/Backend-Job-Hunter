"""Password change revokes other sessions but keeps the current one."""
from .conftest import auth_headers


async def test_change_password_wrong_current_400(client, registered_user):
    res = await client.post(
        "/api/v1/users/me/change-password",
        json={"current_password": "nope-wrong", "new_password": "NewSecret123!"},
        headers=auth_headers(registered_user["tokens"]),
    )
    assert res.status_code == 400


async def test_change_password_revokes_other_sessions_keeps_current(
    client, registered_user, login
):
    other = await login(client, registered_user["email"], registered_user["password"])
    other_tokens = other.json()

    res = await client.post(
        "/api/v1/users/me/change-password",
        json={
            "current_password": registered_user["password"],
            "new_password": "BrandNewSecret1!",
        },
        headers=auth_headers(registered_user["tokens"]),
    )
    assert res.status_code == 200

    # Current session still works
    me = await client.get(
        "/api/v1/users/me", headers=auth_headers(registered_user["tokens"])
    )
    assert me.status_code == 200

    # Other session: access token dead + refresh chain dead
    other_me = await client.get("/api/v1/users/me", headers=auth_headers(other_tokens))
    assert other_me.status_code == 401
    other_refresh = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": other_tokens["refresh_token"]}
    )
    assert other_refresh.status_code == 401

    # Old password no longer logs in; new one does
    old_login = await login(
        client, registered_user["email"], registered_user["password"]
    )
    assert old_login.status_code == 401
    new_login = await login(client, registered_user["email"], "BrandNewSecret1!")
    assert new_login.status_code == 200
