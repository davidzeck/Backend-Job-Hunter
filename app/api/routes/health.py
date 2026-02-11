"""
Health check routes.
"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import redis.asyncio as redis

from app.core.database import get_db
from app.core.config import settings
from app.schemas.base import BaseSchema

router = APIRouter(tags=["health"])


class HealthResponse(BaseSchema):
    """Health check response."""

    status: str
    timestamp: str
    checks: dict


@router.get("/health", response_model=HealthResponse)
async def health_check(db: AsyncSession = Depends(get_db)):
    """
    Health check endpoint for monitoring.

    Returns 200 if all systems are operational.
    """
    checks = {}

    # Check database
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "healthy"
    except Exception as e:
        checks["database"] = f"unhealthy: {str(e)}"

    # Check Redis
    try:
        r = redis.from_url(settings.redis_url)
        await r.ping()
        await r.close()
        checks["redis"] = "healthy"
    except Exception as e:
        checks["redis"] = f"unhealthy: {str(e)}"

    # Overall status
    all_healthy = all(v == "healthy" for v in checks.values())

    return HealthResponse(
        status="healthy" if all_healthy else "degraded",
        timestamp=datetime.now(timezone.utc).isoformat(),
        checks=checks,
    )
