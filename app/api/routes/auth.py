"""
Authentication routes.

ARCHITECTURE RULE: Routes are thin controllers.
They do exactly 3 things:
  1. Extract input from the request (incl. HTTP concerns: forms, cookies,
     headers, client metadata)
  2. Call a service method
  3. Return the result

NO database queries. NO business logic. NO password hashing.
If you see 'select()', 'sqlalchemy', or 'hash_password' here, it's a bug.

Web vs mobile contract:
  - Requests carrying `X-Client: web` get the refresh token as an httpOnly
    cookie and `refresh_token: null` in the body; the cookie is also where
    /auth/refresh and /auth/logout read it from. The mandatory custom header
    doubles as CSRF protection (cross-site forms can't set it).
  - Everything else (Flutter, Swagger, scripts) gets tokens in the body and
    sends the refresh token in the body.
"""
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import limiter, RATE_AUTH, RATE_REFRESH
from app.models.user import User
from app.schemas.auth import (
    ForgotPasswordRequest,
    RefreshTokenRequest,
    RegisterRequest,
    ResetPasswordRequest,
    SessionResponse,
    TokenResponse,
    VerifyEmailRequest,
)
from app.schemas.base import MessageResponse
from app.services.auth_service import AuthService
from app.services.user_service import UserService

router = APIRouter(prefix="/auth", tags=["auth"])

# Service instance - stateless, safe to reuse across requests.
auth_service = AuthService()
user_service = UserService()


# ── HTTP helpers (controller-level concerns only) ───────────────────────

def _is_web(request: Request) -> bool:
    return request.headers.get("X-Client", "").lower() == "web"


def _client_ip(request: Request) -> Optional[str]:
    # Correct behind the Next.js dev proxy once uvicorn runs --proxy-headers
    return request.client.host if request.client else None


def _device_info(request: Request) -> tuple[Optional[str], Optional[str]]:
    """(device, browser) from the User-Agent — coarse, for the sessions UI."""
    ua = request.headers.get("user-agent", "") or None
    browser = None
    if ua:
        for name in ("Edg", "OPR", "Chrome", "Firefox", "Safari", "Dart", "okhttp"):
            if name in ua:
                browser = {"Edg": "Edge", "OPR": "Opera", "Dart": "Flutter"}.get(name, name)
                break
    return (ua[:255] if ua else None), browser


def _set_refresh_cookie(response: Response, token: str, persistent: bool) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
        max_age=settings.refresh_token_expire_days * 86400 if persistent else None,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )


def _finalize_tokens(
    request: Request, response: Response, tokens: TokenResponse, persistent: bool
) -> TokenResponse:
    """Web: move the refresh token from the body into the httpOnly cookie."""
    if _is_web(request):
        _set_refresh_cookie(response, tokens.refresh_token, persistent)
        tokens.refresh_token = None
    return tokens


# ── Endpoints ────────────────────────────────────────────────────────────

@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_AUTH)
async def register(
    request: Request,
    response: Response,
    body: RegisterRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Register a new user. Returns tokens and sends a verification email. Rate limited: 5/min per IP."""
    device, browser = _device_info(request)
    tokens = await auth_service.register(
        db,
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        phone=body.phone,
        client="web" if _is_web(request) else "mobile",
        device=device,
        browser=browser,
        ip=_client_ip(request),
        background_tasks=background_tasks,
    )
    return _finalize_tokens(request, response, tokens, persistent=True)


@router.post("/login", response_model=TokenResponse)
@limiter.limit(RATE_AUTH)
async def login(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    form: OAuth2PasswordRequestForm = Depends(),
):
    """
    Login with the OAuth2 password form (username = email).
    Extra form field `remember_me` controls web cookie persistence.
    Rate limited: 5/min per IP.
    """
    form_data = await request.form()
    remember_me = str(form_data.get("remember_me", "")).lower() in ("true", "1", "on")

    device, browser = _device_info(request)
    tokens = await auth_service.login(
        db,
        email=form.username,
        password=form.password,
        client="web" if _is_web(request) else "mobile",
        device=device,
        browser=browser,
        ip=_client_ip(request),
    )
    return _finalize_tokens(request, response, tokens, persistent=remember_me)


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit(RATE_REFRESH)
async def refresh_token(
    request: Request,
    response: Response,
    body: Optional[RefreshTokenRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Rotate a refresh token. Mobile sends it in the body; web clients send
    `X-Client: web` and the httpOnly cookie. Reusing a rotated token revokes
    the whole session (theft detection).
    """
    raw = body.refresh_token if body and body.refresh_token else None
    if raw is None and _is_web(request):
        raw = request.cookies.get(settings.refresh_cookie_name)

    if not raw:
        from app.core.exceptions import InvalidTokenException

        raise InvalidTokenException()

    device, browser = _device_info(request)
    tokens, user = await auth_service.refresh(
        db, raw_token=raw, device=device, browser=browser, ip=_client_ip(request)
    )
    # Include the profile so web clients bootstrap in one round-trip (no /users/me).
    tokens.user = await user_service.get_profile(db, user)
    # Rotated web cookie stays persistent — remember-me choice is sticky via
    # the original Max-Age; session-cookie users get a session cookie again.
    persistent = bool(request.cookies.get(settings.refresh_cookie_name)) and _is_web(request)
    return _finalize_tokens(request, response, tokens, persistent=persistent)


@router.post("/logout", response_model=MessageResponse)
@limiter.limit(RATE_REFRESH)
async def logout(
    request: Request,
    response: Response,
    body: Optional[RefreshTokenRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Logout: revoke the refresh-token session and denylist the current access
    token. Idempotent — always 200, even with missing/invalid tokens.
    """
    raw = body.refresh_token if body and body.refresh_token else None
    if raw is None and _is_web(request):
        raw = request.cookies.get(settings.refresh_cookie_name)

    access_payload = None
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        from app.core.security import decode_token, verify_token_type

        payload = decode_token(auth_header[7:])
        if payload and verify_token_type(payload, "access"):
            access_payload = payload

    await auth_service.logout(db, raw_refresh=raw, access_payload=access_payload)

    if _is_web(request):
        _clear_refresh_cookie(response)
    return MessageResponse(message="Logged out successfully")


@router.get("/validate")
async def validate_session(current_user: User = Depends(get_current_user)):
    """Lightweight session probe for clients."""
    return {"valid": True, "user_id": str(current_user.id)}


# ── Session management ───────────────────────────────────────────────────

@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List live login sessions (one per device/browser)."""
    payload = getattr(request.state, "token_payload", None) or {}
    return await auth_service.list_sessions(
        db, user=current_user, current_sid=payload.get("sid")
    )


@router.delete("/sessions/{session_id}", response_model=MessageResponse)
async def revoke_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke one login session (its refresh chain + live access tokens)."""
    import uuid as _uuid

    from app.core.exceptions import NotFoundException

    try:
        family_id = _uuid.UUID(session_id)
    except ValueError:
        raise NotFoundException(message="Session not found", code="SESSION_NOT_FOUND")

    await auth_service.revoke_session(db, user=current_user, family_id=family_id)
    return MessageResponse(message="Session revoked")


@router.post("/sessions/revoke-all", response_model=MessageResponse)
async def revoke_all_sessions(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke every other login session, keeping the current one."""
    payload = getattr(request.state, "token_payload", None) or {}
    count = await auth_service.revoke_all_sessions(
        db, user=current_user, except_sid=payload.get("sid")
    )
    return MessageResponse(message=f"Revoked {count} other session(s)")


# ── Email flows ──────────────────────────────────────────────────────────

@router.post("/forgot-password", response_model=MessageResponse)
@limiter.limit(RATE_AUTH)
async def forgot_password(
    request: Request,
    body: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Request a password-reset email. Always 200 (no account enumeration)."""
    await auth_service.forgot_password(
        db, email=body.email, background_tasks=background_tasks
    )
    return MessageResponse(
        message="If that email is registered, a reset link has been sent."
    )


@router.post("/reset-password", response_model=MessageResponse)
@limiter.limit(RATE_AUTH)
async def reset_password(
    request: Request,
    body: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Set a new password with an emailed single-use token. Revokes all sessions."""
    await auth_service.reset_password(
        db, token=body.token, new_password=body.new_password
    )
    return MessageResponse(message="Password has been reset. Please log in.")


@router.post("/verify-email", response_model=MessageResponse)
@limiter.limit(RATE_AUTH)
async def verify_email(
    request: Request,
    body: VerifyEmailRequest,
    db: AsyncSession = Depends(get_db),
):
    """Confirm an email address with an emailed single-use token."""
    await auth_service.verify_email(db, token=body.token)
    return MessageResponse(message="Email verified.")


@router.post("/resend-verification", response_model=MessageResponse)
@limiter.limit(RATE_AUTH)
async def resend_verification(
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Resend the verification email for the logged-in account."""
    await auth_service.resend_verification(
        db, user=current_user, background_tasks=background_tasks
    )
    return MessageResponse(message="Verification email sent.")
