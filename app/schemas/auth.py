"""
Authentication schemas.
"""
from typing import Optional
from pydantic import EmailStr, Field
from app.schemas.base import BaseSchema


class LoginRequest(BaseSchema):
    """Login request body."""

    email: EmailStr
    password: str = Field(..., min_length=6)


class RegisterRequest(BaseSchema):
    """Registration request body."""

    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=20)


class TokenResponse(BaseSchema):
    """Token response after successful authentication."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class RefreshTokenRequest(BaseSchema):
    """Refresh token request body."""

    refresh_token: str


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
