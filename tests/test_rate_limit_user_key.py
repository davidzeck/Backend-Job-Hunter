"""Per-user rate-limit keying (the request.state.current_user fix)."""
from .conftest import auth_headers


async def test_authenticated_limit_keyed_per_user_not_ip(
    client, registered_user, admin_user
):
    """
    Both test users share the ASGI client "IP". With user-keyed limits each
    gets an independent 2/minute budget on the probe route; with IP keying
    the second user's first call would already be the third hit and 429.
    """
    h1 = auth_headers(registered_user["tokens"])
    h2 = auth_headers(admin_user["tokens"])

    assert (await client.get("/api/v1/_test/limited", headers=h1)).status_code == 200
    assert (await client.get("/api/v1/_test/limited", headers=h1)).status_code == 200
    assert (await client.get("/api/v1/_test/limited", headers=h1)).status_code == 429

    # Different user, same IP — separate budget
    assert (await client.get("/api/v1/_test/limited", headers=h2)).status_code == 200


async def test_request_state_current_user_set(client, registered_user):
    res = await client.get(
        "/api/v1/_test/limited", headers=auth_headers(registered_user["tokens"])
    )
    assert res.status_code == 200
    assert res.json()["user_id"]  # echoed from request-scoped current_user
