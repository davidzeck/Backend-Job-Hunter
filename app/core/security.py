"""
Security utilities for authentication and authorization.
Handles JWT tokens and password hashing.

Token model (see docs/security.md):
- Access tokens are stateless JWTs (30 min) carrying sub/sid/jti/iat/exp/type.
- Refresh tokens are JWTs whose `jti` is the primary key of an `auth_sessions`
  row; `sid` is the session family id (stable across rotations).
"""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings


# Password hashing context (rounds pinned for explicitness; tests lower it)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

# Tolerated clock skew when validating exp/iat
_JWT_LEEWAY_SECONDS = 10


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash."""
    return pwd_context.verify(plain_password, hashed_password)


def hash_password(password: str) -> str:
    """Hash a password."""
    return pwd_context.hash(password)


def hash_token(raw_token: str) -> str:
    """
    SHA-256 hex digest of an opaque token string.

    Used for refresh JWTs (stored on auth_sessions.token_hash) and single-use
    email tokens — raw secrets never touch the database.
    """
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_access_token(
    user_id: str,
    session_id: str,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Create a JWT access token bound to a login session.

    Args:
        user_id: User UUID (becomes `sub`)
        session_id: Session family UUID (becomes `sid`) — lets one Redis
            marker revoke every outstanding access token of a session
        expires_delta: Custom expiration time

    Returns:
        Encoded JWT token string
    """
    now = datetime.now(timezone.utc)
    expire = now + (
        expires_delta
        if expires_delta
        else timedelta(minutes=settings.access_token_expire_minutes)
    )
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": expire,
        "type": "access",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def create_refresh_token(user_id: str, session_id: str, token_id: str) -> str:
    """
    Create a JWT refresh token.

    Args:
        user_id: User UUID (`sub`)
        session_id: Session family UUID (`sid`)
        token_id: The auth_sessions row id for THIS token (`jti`)

    Returns:
        Encoded JWT token string
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "jti": str(token_id),
        "iat": now,
        "exp": now + timedelta(days=settings.refresh_token_expire_days),
        "type": "refresh",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str) -> Optional[dict[str, Any]]:
    """
    Decode and validate a JWT token.

    Args:
        token: JWT token string

    Returns:
        Decoded payload or None if invalid
    """
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],
            options={"leeway": _JWT_LEEWAY_SECONDS},
        )
        return payload
    except JWTError:
        return None


def verify_token_type(payload: dict[str, Any], expected_type: str) -> bool:
    """Verify that a token is of the expected type."""
    return payload.get("type") == expected_type
