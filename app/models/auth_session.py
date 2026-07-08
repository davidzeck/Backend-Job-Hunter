"""
AuthSession model — one row per refresh token.

A login creates the first row of a "family" (family_id == that row's id).
Every refresh creates a child row and marks the old one replaced_by=child.
Presenting a replaced/revoked token outside the grace window revokes the
whole family (token-theft reuse detection).
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel


class AuthSession(BaseModel):
    """A single refresh token within a login-session family."""

    __tablename__ = "auth_sessions"

    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # sha256 hex of the raw refresh JWT — raw tokens never stored
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    client: Mapped[str] = mapped_column(String(10), nullable=False, default="web")
    device: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    browser: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)

    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    replaced_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("auth_sessions.id"), nullable=True
    )

    user = relationship("User", back_populates="auth_sessions")

    __table_args__ = (
        Index("ix_auth_sessions_user_active", "user_id", "revoked_at"),
    )

    @property
    def is_active_tail(self) -> bool:
        """True if this is the current (rotatable) token of its family."""
        return self.revoked_at is None and self.replaced_by is None

    def __repr__(self) -> str:
        return f"<AuthSession {self.id} family={self.family_id} user={self.user_id}>"
