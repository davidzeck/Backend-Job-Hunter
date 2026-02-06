"""
Custom exceptions for the application.
All API exceptions should inherit from APIException for consistent error handling.
"""
from typing import Optional, Any


class APIException(Exception):
    """
    Base exception for all API errors.
    Provides consistent error response format.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Optional[Any] = None,
    ):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        super().__init__(self.message)


class BadRequestException(APIException):
    """400 Bad Request"""

    def __init__(self, message: str = "Bad request", code: str = "BAD_REQUEST"):
        super().__init__(400, code, message)


class UnauthorizedException(APIException):
    """401 Unauthorized"""

    def __init__(self, message: str = "Unauthorized", code: str = "UNAUTHORIZED"):
        super().__init__(401, code, message)


class ForbiddenException(APIException):
    """403 Forbidden"""

    def __init__(self, message: str = "Forbidden", code: str = "FORBIDDEN"):
        super().__init__(403, code, message)


class NotFoundException(APIException):
    """404 Not Found"""

    def __init__(self, message: str = "Resource not found", code: str = "NOT_FOUND"):
        super().__init__(404, code, message)


class ConflictException(APIException):
    """409 Conflict"""

    def __init__(self, message: str = "Resource conflict", code: str = "CONFLICT"):
        super().__init__(409, code, message)


class ValidationException(APIException):
    """422 Validation Error"""

    def __init__(
        self,
        message: str = "Validation error",
        code: str = "VALIDATION_ERROR",
        details: Optional[Any] = None,
    ):
        super().__init__(422, code, message, details)


class InternalServerException(APIException):
    """500 Internal Server Error"""

    def __init__(
        self,
        message: str = "Internal server error",
        code: str = "INTERNAL_ERROR",
    ):
        super().__init__(500, code, message)


# Authentication specific exceptions
class InvalidCredentialsException(UnauthorizedException):
    """Invalid email or password"""

    def __init__(self):
        super().__init__(
            message="Invalid email or password",
            code="INVALID_CREDENTIALS",
        )


class TokenExpiredException(UnauthorizedException):
    """Token has expired"""

    def __init__(self):
        super().__init__(
            message="Token has expired",
            code="TOKEN_EXPIRED",
        )


class InvalidTokenException(UnauthorizedException):
    """Token is invalid"""

    def __init__(self):
        super().__init__(
            message="Invalid token",
            code="INVALID_TOKEN",
        )


# Resource specific exceptions
class UserNotFoundException(NotFoundException):
    """User not found"""

    def __init__(self):
        super().__init__(message="User not found", code="USER_NOT_FOUND")


class JobNotFoundException(NotFoundException):
    """Job not found"""

    def __init__(self):
        super().__init__(message="Job not found", code="JOB_NOT_FOUND")


class SourceNotFoundException(NotFoundException):
    """Job source not found"""

    def __init__(self):
        super().__init__(message="Job source not found", code="SOURCE_NOT_FOUND")


class CompanyNotFoundException(NotFoundException):
    """Company not found"""

    def __init__(self):
        super().__init__(message="Company not found", code="COMPANY_NOT_FOUND")


class EmailAlreadyExistsException(ConflictException):
    """Email already registered"""

    def __init__(self):
        super().__init__(
            message="Email already registered",
            code="EMAIL_EXISTS",
        )
