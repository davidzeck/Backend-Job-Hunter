"""
Authentication routes.

ARCHITECTURE RULE: Routes are thin controllers.
They do exactly 3 things:
  1. Extract input from the request
  2. Call a service method
  3. Return the result

NO database queries. NO business logic. NO password hashing.
If you see 'select()', 'sqlalchemy', or 'hash_password' here, it's a bug.
"""
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rate_limit import limiter, RATE_AUTH
from app.services.auth_service import AuthService
from app.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    RefreshTokenRequest,
)
from app.schemas.base import MessageResponse

router = APIRouter(prefix="/auth", tags=["auth"])

# Service instance - stateless, safe to reuse across requests.
# Why not instantiate per-request? Services hold no state (no self.db).
# The db session is passed as a parameter, so one instance serves all requests.
auth_service = AuthService()


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_AUTH)
async def register(
    request: Request,
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """Register a new user. Returns access and refresh tokens. Rate limited: 5/min per IP."""
    return await auth_service.register(
        db,
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        phone=body.phone,
    )


@router.post("/login", response_model=TokenResponse)
@limiter.limit(RATE_AUTH)
async def login(
    request: Request,
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """Login with email and password. Returns access and refresh tokens. Rate limited: 5/min per IP."""
    return await auth_service.login(
        db,
        email=body.email,
        password=body.password,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
):
    """Refresh access token using a valid refresh token."""
    return await auth_service.refresh(
        db,
        refresh_token=request.refresh_token,
    )


@router.post("/logout", response_model=MessageResponse)
async def logout():
    """
    Logout current user.

    With JWT, logout is handled client-side by discarding tokens.
    Exists for API completeness and future token blacklisting.
    """
    return MessageResponse(message="Logged out successfully")
