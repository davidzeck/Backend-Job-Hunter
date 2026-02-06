"""
Pydantic schemas for API validation and serialization.
"""
from app.schemas.base import (
    BaseSchema,
    PaginatedResponse,
    MessageResponse,
    ErrorResponse,
)
from app.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    RefreshTokenRequest,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
)
from app.schemas.user import (
    UserCreate,
    UserUpdate,
    UserResponse,
    UserProfileResponse,
    UserPreferences,
    UpdateFCMTokenRequest,
)
from app.schemas.job import (
    JobListItem,
    JobDetail,
    JobFilters,
    SkillGapResponse,
    CompanyBrief,
    JobSkillResponse,
)
from app.schemas.company import (
    CompanyCreate,
    CompanyUpdate,
    CompanyResponse,
)
from app.schemas.source import (
    SourceCreate,
    SourceUpdate,
    SourceResponse,
    SourceHealthResponse,
    TriggerScrapeResponse,
    ScrapeLogResponse,
)
from app.schemas.alert import (
    AlertResponse,
    AlertUpdate,
)

__all__ = [
    # Base
    "BaseSchema",
    "PaginatedResponse",
    "MessageResponse",
    "ErrorResponse",
    # Auth
    "LoginRequest",
    "RegisterRequest",
    "TokenResponse",
    "RefreshTokenRequest",
    "ChangePasswordRequest",
    "ForgotPasswordRequest",
    "ResetPasswordRequest",
    # User
    "UserCreate",
    "UserUpdate",
    "UserResponse",
    "UserProfileResponse",
    "UserPreferences",
    "UpdateFCMTokenRequest",
    # Job
    "JobListItem",
    "JobDetail",
    "JobFilters",
    "SkillGapResponse",
    "CompanyBrief",
    "JobSkillResponse",
    # Company
    "CompanyCreate",
    "CompanyUpdate",
    "CompanyResponse",
    # Source
    "SourceCreate",
    "SourceUpdate",
    "SourceResponse",
    "SourceHealthResponse",
    "TriggerScrapeResponse",
    "ScrapeLogResponse",
    # Alert
    "AlertResponse",
    "AlertUpdate",
]
