"""
Authentication service - handles registration, login, and token management.
"""
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    verify_password,
    hash_password,
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_token_type,
)
from app.core.exceptions import (
    InvalidCredentialsException,
    EmailAlreadyExistsException,
    InvalidTokenException,
)
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.auth import TokenResponse


class AuthService:
    """Handles all authentication business logic."""

    def __init__(self):
        self.user_repo = UserRepository()

    async def register(
        self,
        db: AsyncSession,
        *,
        email: str,
        password: str,
        full_name: str,
        phone: str = None,
    ) -> TokenResponse:
        """
        Register a new user and return tokens.

        Raises:
            EmailAlreadyExistsException: If email is already registered.
        """
        if await self.user_repo.email_exists(db, email):
            raise EmailAlreadyExistsException()

        user = await self.user_repo.create(
            db,
            email=email,
            password_hash=hash_password(password),
            full_name=full_name,
            phone=phone,
        )
        await db.commit()

        return self._generate_tokens(user)

    async def login(
        self,
        db: AsyncSession,
        *,
        email: str,
        password: str,
    ) -> TokenResponse:
        """
        Authenticate user and return tokens.

        Raises:
            InvalidCredentialsException: If email/password is wrong.
        """
        user = await self.user_repo.get_active_by_email(db, email)

        if not user or not verify_password(password, user.password_hash):
            raise InvalidCredentialsException()

        # Update last seen
        user.last_seen_at = datetime.now(timezone.utc)
        await db.commit()

        return self._generate_tokens(user)

    async def refresh(
        self,
        db: AsyncSession,
        *,
        refresh_token: str,
    ) -> TokenResponse:
        """
        Issue new tokens using a valid refresh token.

        Raises:
            InvalidTokenException: If refresh token is invalid or expired.
        """
        payload = decode_token(refresh_token)

        if not payload or not verify_token_type(payload, "refresh"):
            raise InvalidTokenException()

        user_id = payload.get("sub")
        if not user_id:
            raise InvalidTokenException()

        user = await self.user_repo.get_active_by_id(db, user_id)
        if not user:
            raise InvalidTokenException()

        return self._generate_tokens(user)

    def _generate_tokens(self, user: User) -> TokenResponse:
        """Generate access and refresh tokens for a user."""
        access_token = create_access_token({"sub": str(user.id)})
        refresh_token = create_refresh_token({"sub": str(user.id)})

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.access_token_expire_minutes * 60,
        )
