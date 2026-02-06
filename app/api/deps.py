"""
API dependencies for dependency injection.
"""
from typing import Optional
from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import decode_token, verify_token_type
from app.core.exceptions import (
    UnauthorizedException,
    InvalidTokenException,
    ForbiddenException,
)
from app.models.user import User


# Security scheme
security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Get the current authenticated user.

    Raises:
        UnauthorizedException: If no token provided
        InvalidTokenException: If token is invalid
        TokenExpiredException: If token has expired
    """
    if not credentials:
        raise UnauthorizedException("Authentication required")

    token = credentials.credentials
    payload = decode_token(token)

    if not payload:
        raise InvalidTokenException()

    if not verify_token_type(payload, "access"):
        raise InvalidTokenException()

    user_id = payload.get("sub")
    if not user_id:
        raise InvalidTokenException()

    # Fetch user from database
    result = await db.execute(
        select(User).where(User.id == user_id, User.is_active == True)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise InvalidTokenException()

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
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """
    Get the current user if authenticated, None otherwise.
    Useful for endpoints that work with or without authentication.
    """
    if not credentials:
        return None

    try:
        return await get_current_user(credentials, db)
    except UnauthorizedException:
        return None
