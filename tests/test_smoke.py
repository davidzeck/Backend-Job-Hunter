async def test_health_ok(client):
    res = await client.get("/api/v1/health")
    assert res.status_code == 200
