"""
Redis-backed access-token revocation.

Access tokens are stateless JWTs, so instant revocation (logout, password
change, session revoke) needs a shared marker both the API workers can see:

  auth:revoked_sid:{sid}   — set when a whole session family is revoked;
                             kills every outstanding access token of that
                             login in one key. TTL = access-token lifetime
                             (after that, all its access tokens are expired
                             anyway).
  auth:denylist:jti:{jti}  — a single access token, TTL = its remaining life.

Availability policy: checks FAIL OPEN on Redis errors (logged loudly).
Refresh-token validation is Postgres-backed and fails closed, so a Redis
outage widens the revocation window to at most the access TTL (30 min) —
it never allows a new token to be minted for a revoked session.
"""
from typing import Optional

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_SID_PREFIX = "auth:revoked_sid:"
_JTI_PREFIX = "auth:denylist:jti:"

_redis_pool: Optional[aioredis.Redis] = None


def _get_redis() -> aioredis.Redis:
    """Lazy-init a shared async Redis connection (same pattern as rate_limit)."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis_pool


async def reset_pool() -> None:
    """Close the shared pool (tests recreate event loops per test)."""
    global _redis_pool
    if _redis_pool is not None:
        try:
            await _redis_pool.aclose()
        except Exception:  # noqa: BLE001 — best-effort close
            pass
        _redis_pool = None


async def revoke_session_marker(sid: str) -> None:
    """Mark a session family revoked; all its access tokens die immediately."""
    ttl = settings.access_token_expire_minutes * 60
    try:
        await _get_redis().setex(f"{_SID_PREFIX}{sid}", ttl, "1")
    except Exception as exc:  # noqa: BLE001
        logger.error("denylist_marker_failed", sid=sid, error=str(exc))


async def denylist_access_jti(jti: str, ttl_seconds: int) -> None:
    """Deny a single access token for its remaining lifetime."""
    if ttl_seconds <= 0:
        return
    try:
        await _get_redis().setex(f"{_JTI_PREFIX}{jti}", ttl_seconds, "1")
    except Exception as exc:  # noqa: BLE001
        logger.error("denylist_jti_failed", jti=jti, error=str(exc))


async def is_access_revoked(jti: str, sid: str) -> bool:
    """
    True if this access token (or its whole session) was revoked.
    Fails open (returns False) on Redis errors — logged for alerting.
    """
    try:
        r = _get_redis()
        async with r.pipeline(transaction=False) as pipe:
            pipe.exists(f"{_JTI_PREFIX}{jti}")
            pipe.exists(f"{_SID_PREFIX}{sid}")
            jti_hit, sid_hit = await pipe.execute()
        return bool(jti_hit or sid_hit)
    except Exception as exc:  # noqa: BLE001
        logger.error("denylist_check_failed", jti=jti, sid=sid, error=str(exc))
        return False
