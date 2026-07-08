"""
Authentication schemas.

Note: login has NO JSON schema — it accepts OAuth2PasswordRequestForm
(form-encoded username/password/remember_me) per the standard flow.
"""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import EmailStr, Field

from app.schemas.base import BaseSchema


class RegisterRequest(BaseSchema):
    """Registration request body."""

    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=20)


class TokenResponse(BaseSchema):
    """Token response after successful authentication.

    refresh_token is None for web clients (X-Client: web) — they receive it
    as an httpOnly cookie instead.
    """

    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    expires_in: int  # seconds


class RefreshTokenRequest(BaseSchema):
    """Refresh token request body (mobile). Web clients use the cookie."""

    refresh_token: Optional[str] = None


class ChangePasswordRequest(BaseSchema):
    """Change password request body."""

    current_password: str
    new_password: str = Field(..., min_length=8)


class ForgotPasswordRequest(BaseSchema):
    """Forgot password request body."""

    email: EmailStr


class ResetPasswordRequest(BaseSchema):
    """Reset password request body."""

    token: str
    new_password: str = Field(..., min_length=8)


class VerifyEmailRequest(BaseSchema):
    """Email verification request body."""

    token: str


class SessionResponse(BaseSchema):
    """A live login session (refresh-token family) for the settings UI."""

    id: uuid.UUID  # family_id — pass to DELETE /auth/sessions/{id}
    device: Optional[str] = None
    browser: Optional[str] = None
    ip_address: Optional[str] = None
    location: Optional[str] = None
    last_active: datetime
    created_at: datetime
    is_current: bool = False
