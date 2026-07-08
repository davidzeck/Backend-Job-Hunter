"""Session management endpoints (list / revoke / revoke-all)."""
from .conftest import auth_headers


async def _login_new_session(client, login, user) -> dict:
    res = await login(client, user["email"], user["password"])
    assert res.status_code == 200
    return res.json()


async def test_list_sessions_marks_current(client, registered_user, login):
    second = await _login_new_session(client, login, registered_user)

    res = await client.get(
        "/api/v1/auth/sessions", headers=auth_headers(second)
    )
    assert res.status_code == 200
    sessions = res.json()
    assert len(sessions) == 2  # register session + this login
    current_flags = [s["is_current"] for s in sessions]
    assert current_flags.count(True) == 1


async def test_rotation_keeps_stable_session_id(client, registered_user):
    tokens = registered_user["tokens"]
    before = await client.get("/api/v1/auth/sessions", headers=auth_headers(tokens))
    sid_before = before.json()[0]["id"]

    rotated = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    new_tokens = rotated.json()

    after = await client.get("/api/v1/auth/sessions", headers=auth_headers(new_tokens))
    assert after.status_code == 200
    assert len(after.json()) == 1  # still ONE login session
    assert after.json()[0]["id"] == sid_before  # family id is stable


async def test_revoke_specific_session_kills_its_tokens(client, registered_user, login):
    first = registered_user["tokens"]
    second = await _login_new_session(client, login, registered_user)

    # From the second session, find and revoke the first
    listing = await client.get("/api/v1/auth/sessions", headers=auth_headers(second))
    other = next(s for s in listing.json() if not s["is_current"])

    res = await client.delete(
        f"/api/v1/auth/sessions/{other['id']}", headers=auth_headers(second)
    )
    assert res.status_code == 200

    # First session's refresh chain is dead
    refresh = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert refresh.status_code == 401
    # ...and its access token too (Redis sid marker)
    me = await client.get("/api/v1/users/me", headers=auth_headers(first))
    assert me.status_code == 401
    # The revoking session is untouched
    still_me = await client.get("/api/v1/users/me", headers=auth_headers(second))
    assert still_me.status_code == 200


async def test_revoke_foreign_session_404(client, registered_user, admin_user):
    mine = await client.get(
        "/api/v1/auth/sessions", headers=auth_headers(registered_user["tokens"])
    )
    my_session_id = mine.json()[0]["id"]

    res = await client.delete(
        f"/api/v1/auth/sessions/{my_session_id}",
        headers=auth_headers(admin_user["tokens"]),
    )
    assert res.status_code == 404


async def test_revoke_all_keeps_current_session_alive(client, registered_user, login):
    await _login_new_session(client, login, registered_user)
    third = await _login_new_session(client, login, registered_user)

    res = await client.post(
        "/api/v1/auth/sessions/revoke-all", headers=auth_headers(third)
    )
    assert res.status_code == 200

    listing = await client.get("/api/v1/auth/sessions", headers=auth_headers(third))
    remaining = listing.json()
    assert len(remaining) == 1
    assert remaining[0]["is_current"] is True
