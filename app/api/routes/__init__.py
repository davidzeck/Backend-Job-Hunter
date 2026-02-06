"""
API Routes package.
"""
from fastapi import APIRouter

from app.api.routes.auth import router as auth_router
from app.api.routes.health import router as health_router
from app.api.routes.users import router as users_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.companies import router as companies_router
from app.api.routes.alerts import router as alerts_router

# Main API router
api_router = APIRouter()

# Include all routers
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(jobs_router)
api_router.include_router(companies_router)
api_router.include_router(alerts_router)

__all__ = [
    "api_router",
    "auth_router",
    "health_router",
    "users_router",
    "jobs_router",
    "companies_router",
    "alerts_router",
]
