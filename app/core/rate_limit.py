"""
Rate limiting configuration using slowapi.

Uses Redis as the backend so limits are shared across workers.
Provides pre-configured limiters for different endpoint categories.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

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
RATE_CV_UPLOAD = "3/hour"        # presign — prevents S3 quota abuse
RATE_AI = "10/hour"              # analyze, tailor — OpenAI cost control
RATE_TASK_POLL = "60/minute"     # task status polling
RATE_DEFAULT = "60/minute"       # general API fallback
