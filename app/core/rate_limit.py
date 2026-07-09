"""
Rate limiting configuration using slowapi.

Uses Redis as the backend so limits are shared across workers.
Provides pre-configured limiters for different endpoint categories.

Also provides a daily AI usage cap (Redis counter) to prevent cost abuse
beyond the per-hour slowapi limits.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

import redis.asyncio as aioredis

from app.core.config import settings


def _get_user_or_ip(request: Request) -> str:
    """
    Rate-limit key: authenticated user ID if available, otherwise client IP.

    This ensures per-user limits for authenticated endpoints and
    per-IP limits for unauthenticated ones (login, register).
    """
    # Check if user was injected by auth dependency
    user = getattr(request.state, "current_user", None)
    if user and hasattr(user, "id"):
        return str(user.id)
    return get_remote_address(request)


limiter = Limiter(
    key_func=_get_user_or_ip,
    storage_uri=settings.redis_url,
    strategy="fixed-window",
)

# Pre-defined rate limit strings for use in route decorators:
#   @limiter.limit(RATE_AUTH)
RATE_AUTH = "5/minute"           # login, register — brute-force protection
RATE_REFRESH = "30/minute"       # token refresh / logout
RATE_CV_UPLOAD = "3/hour"        # presign — prevents S3 quota abuse
RATE_AI = "10/hour"              # analyze, tailor — Gemini cost control
RATE_TASK_POLL = "60/minute"     # task status polling
RATE_DEFAULT = "60/minute"       # general API fallback

# Daily AI usage cap (per user). Enforced in addition to the hourly limit.
_DAILY_AI_CAP = 50
_DAILY_AI_KEY_PREFIX = "ai_daily:"

_redis_pool: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    """Lazy-init a shared async Redis connection for the daily cap counter."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis_pool


async def check_ai_daily_cap(user_id: str) -> bool:
    """
    Increment the daily AI call counter for a user and return True if allowed.

    Returns False (over limit) when the user has exceeded _DAILY_AI_CAP calls
    for the current UTC day. The key auto-expires after 24 hours.
    """
    r = _get_redis()
    key = f"{_DAILY_AI_KEY_PREFIX}{user_id}"
    count = await r.incr(key)
    if count == 1:
        # First call today — set 24h TTL
        await r.expire(key, 86400)
    return count <= _DAILY_AI_CAP


async def get_ai_daily_usage(user_id: str) -> int:
    """Return current daily AI call count for the user (0 if no calls today)."""
    r = _get_redis()
    val = await r.get(f"{_DAILY_AI_KEY_PREFIX}{user_id}")
    return int(val) if val else 0


# Warn the user when this many calls (or fewer) remain in the daily cap.
AI_WARN_THRESHOLD = 10


async def get_ai_usage(user_id: str) -> dict:
    """
    Usage snapshot for the client: used/limit/remaining, whether we're in the
    warning zone, and seconds until the counter resets (key TTL; None if the
    user hasn't made a call today).
    """
    r = _get_redis()
    key = f"{_DAILY_AI_KEY_PREFIX}{user_id}"
    val = await r.get(key)
    used = int(val) if val else 0
    remaining = max(_DAILY_AI_CAP - used, 0)
    ttl = await r.ttl(key)  # -2 = no key, -1 = no TTL
    return {
        "used": used,
        "limit": _DAILY_AI_CAP,
        "remaining": remaining,
        "warn": 0 < remaining <= AI_WARN_THRESHOLD,
        "exhausted": remaining == 0,
        "resets_in_seconds": ttl if ttl and ttl > 0 else None,
    }
