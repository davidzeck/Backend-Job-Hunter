"""
Repository for single-use emailed tokens (password reset / email verify).
"""
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_token import EmailToken
from app.repositories.base import BaseRepository


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EmailTokenRepository(BaseRepository[EmailToken]):
    """Data access for email_tokens."""

    def __init__(self):
        super().__init__(EmailToken)

    async def create_token(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        purpose: str,
        token_hash: str,
        expires_at: datetime,
    ) -> EmailToken:
        token = EmailToken(
            user_id=user_id,
            purpose=purpose,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        db.add(token)
        await db.flush()
        return token

    async def get_valid_by_hash(
        self, db: AsyncSession, token_hash: str, purpose: str
    ) -> Optional[EmailToken]:
        """Unused, unexpired token matching hash + purpose."""
        result = await db.execute(
            select(EmailToken).where(
                EmailToken.token_hash == token_hash,
                EmailToken.purpose == purpose,
                EmailToken.used_at.is_(None),
                EmailToken.expires_at > _now(),
            )
        )
        return result.scalar_one_or_none()

    async def invalidate_outstanding(
        self, db: AsyncSession, user_id: UUID, purpose: str
    ) -> None:
        """Burn any previously issued tokens of this purpose for the user."""
        await db.execute(
            update(EmailToken)
            .where(
                EmailToken.user_id == user_id,
                EmailToken.purpose == purpose,
                EmailToken.used_at.is_(None),
            )
            .values(used_at=_now())
        )
