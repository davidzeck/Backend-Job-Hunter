"""
API dependencies for dependency injection.
"""
from typing import Optional

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.denylist import is_access_revoked
from app.core.exceptions import (
    ForbiddenException,
    InvalidTokenException,
    TokenRevokedException,
    UnauthorizedException,
)
from app.core.security import decode_token, verify_token_type
from app.models.user import User

# OAuth2 password flow — makes Swagger's Authorize button work end-to-end.
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login",
    auto_error=False,
)


async def get_current_user(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Get the current authenticated user.

    Validates: token presence → signature/exp → type=access → jti/sid claims
    (legacy tokens without them are rejected) → Redis revocation markers →
    active user in DB.

    Side effect: sets request.state.current_user and request.state.token_payload
    so the rate limiter keys by user id and routes can read the session id.
    """
    if not token:
        raise UnauthorizedException("Authentication required")

    payload = decode_token(token)

    if not payload:
        raise InvalidTokenException()

    if not verify_token_type(payload, "access"):
        raise InvalidTokenException()

    user_id = payload.get("sub")
    jti = payload.get("jti")
    sid = payload.get("sid")
    if not user_id or not jti or not sid:
        # Tokens minted before the session model existed
        raise InvalidTokenException()

    if await is_access_revoked(jti, sid):
        raise TokenRevokedException()

    # Fetch user from database
    result = await db.execute(
        select(User).where(User.id == user_id, User.is_active == True)  # noqa: E712
    )
    user = result.scalar_one_or_none()

    if not user:
        raise InvalidTokenException()

    # Expose auth context for rate limiting and session-aware routes
    request.state.current_user = user
    request.state.token_payload = payload

    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Get current user, ensuring they are active."""
    if not current_user.is_active:
        raise ForbiddenException("User account is disabled")
    return current_user


async def get_admin_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Get current user, ensuring they are an admin.

    Raises:
        ForbiddenException: If user is not an admin
    """
    if not current_user.is_admin:
        raise ForbiddenException("Admin access required")
    return current_user


async def get_optional_user(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """
    Get the current user if authenticated, None otherwise.
    Useful for endpoints that work with or without authentication.
    """
    if not token:
        return None

    try:
        return await get_current_user(request, token, db)
    except UnauthorizedException:
        return None
