"""
Test fixtures for the Job Scout backend.

Environment: real Postgres (auto-created `jobscout_test` database) and real
Redis (db 15, flushed per test). Env overrides MUST happen before any `app.*`
import — settings are read at import time.

Event-loop notes (pytest-asyncio 0.23, asyncio_mode=auto): each test gets its
own loop, but the SQLAlchemy async engine and any module-level aioredis pools
cache loop-bound connections. Therefore every test tears down by disposing the
engine and resetting those pools.

Run with the compose infra up:  docker compose up -d db redis
"""
import asyncio
import os
import re
import uuid

# ── Env overrides BEFORE importing the app ─────────────────────────────
# Postgres.app instance (trust auth for the local user). The compose db on
# 5432 is shadowed by a system Postgres with unknown credentials on this Mac.
os.environ["DATABASE_URL"] = (
    "postgresql+asyncpg://macbook@localhost:5433/jobscout_test"
)
os.environ["REDIS_URL"] = "redis://localhost:6379/15"
os.environ["ENVIRONMENT"] = "development"
os.environ["DEBUG"] = "true"
os.environ["GEMINI_API_KEY"] = ""  # AI must never be called from tests

import asyncpg  # noqa: E402
import pytest  # noqa: E402
import redis.asyncio as aioredis  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core import security  # noqa: E402

# Fast bcrypt for tests (production uses higher rounds)
security.pwd_context.update(bcrypt__rounds=4)

from app.main import app  # noqa: E402
from app.core.database import Base, engine  # noqa: E402

TEST_DB_NAME = "jobscout_test"
_ADMIN_DSN = "postgresql://macbook@localhost:5433/postgres"


# ── Throwaway rate-limited route for the per-user keying test ──────────
from fastapi import APIRouter, Depends, Request  # noqa: E402

from app.api.deps import get_current_user  # noqa: E402
from app.core.rate_limit import limiter  # noqa: E402
from app.models.user import User  # noqa: E402

_test_router = APIRouter()


@_test_router.get("/_test/limited")
@limiter.limit("2/minute")
async def _limited_probe(request: Request, current_user: User = Depends(get_current_user)):
    return {"ok": True, "user_id": str(current_user.id)}


app.include_router(_test_router, prefix="/api/v1")


# ── Database lifecycle (sync session fixture; own asyncio.run loop) ────
@pytest.fixture(scope="session", autouse=True)
def _test_database():
    async def _setup():
        conn = await asyncpg.connect(_ADMIN_DSN)
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", TEST_DB_NAME
        )
        if not exists:
            await conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
        await conn.close()

        async with engine.begin() as db:
            await db.run_sync(Base.metadata.create_all)
        await engine.dispose()  # release connections bound to this setup loop

    asyncio.run(_setup())
    yield


async def _reset_shared_pools():
    """Dispose loop-bound clients so the next test's loop starts clean."""
    await engine.dispose()
    try:
        from app.core import denylist

        await denylist.reset_pool()
    except ImportError:
        pass
    try:
        from app.core import rate_limit

        if rate_limit._redis_pool is not None:
            await rate_limit._redis_pool.aclose()
            rate_limit._redis_pool = None
    except Exception:
        pass


@pytest.fixture(autouse=True)
async def _clean_state(_test_database):
    """Truncate all tables and flush Redis before every test."""
    table_names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as db:
        await db.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))
    r = aioredis.from_url(settings.redis_url)
    await r.flushdb()
    await r.aclose()
    yield
    await _reset_shared_pools()


# ── HTTP client ─────────────────────────────────────────────────────────
@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── User factories ──────────────────────────────────────────────────────
async def _register(client: AsyncClient, email: str, password: str, **extra) -> dict:
    res = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Test User", **extra},
    )
    assert res.status_code == 201, res.text
    return res.json()


@pytest.fixture
async def registered_user(client):
    """Register a fresh user; returns dict(email, password, tokens)."""
    email = f"user-{uuid.uuid4().hex[:10]}@test.jobscout.com"
    password = "Sup3rSecret!pw"
    tokens = await _register(client, email, password)
    return {"email": email, "password": password, "tokens": tokens}


@pytest.fixture
async def admin_user(client):
    """Register a user then flip is_admin directly in the DB."""
    email = f"admin-{uuid.uuid4().hex[:10]}@test.jobscout.com"
    password = "Adm1nSecret!pw"
    tokens = await _register(client, email, password)

    async with engine.begin() as db:
        await db.execute(
            text("UPDATE users SET is_admin = true WHERE email = :email"),
            {"email": email},
        )
    return {"email": email, "password": password, "tokens": tokens}


def auth_headers(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture
async def login():
    """Form-encoded login helper: login(client, email, password, web=False)."""

    async def _login(
        client: AsyncClient,
        email: str,
        password: str,
        web: bool = False,
        remember: bool = False,
    ):
        headers = {"X-Client": "web"} if web else {}
        data = {"username": email, "password": password}
        if remember:
            data["remember_me"] = "true"
        return await client.post("/api/v1/auth/login", data=data, headers=headers)

    return _login


# ── Captured outbound email ────────────────────────────────────────────
@pytest.fixture
def sent_emails(monkeypatch):
    """Capture EmailService sends; exposes .last_token() helper."""
    captured: list[dict] = []

    from app.services import email_service as email_module

    async def _fake_send(self, to: str, subject: str, html: str):
        captured.append({"to": to, "subject": subject, "html": html})

    monkeypatch.setattr(email_module.EmailService, "send", _fake_send)

    class _Box:
        def __init__(self, items):
            self.items = items

        def last_token(self) -> str:
            assert self.items, "no email captured"
            m = re.search(r"token=([A-Za-z0-9_\-]+)", self.items[-1]["html"])
            assert m, f"no token link in email: {self.items[-1]['html']}"
            return m.group(1)

    return _Box(captured)
