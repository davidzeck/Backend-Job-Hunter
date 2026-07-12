"""
Authentication service - registration, login, rotating refresh tokens,
revocation, and session management.

Model: stateless JWT access tokens + server-side tracked refresh tokens.
Every refresh token is an `auth_sessions` row; a login session is a "family"
of rows chained by `replaced_by`. See docs/security.md.
"""
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.denylist import denylist_access_jti, revoke_session_marker
from app.core.exceptions import (
    EmailAlreadyExistsException,
    InvalidCredentialsException,
    InvalidTokenException,
    NotFoundException,
    TokenReuseException,
)
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    verify_password,
    verify_token_type,
)
from app.models.auth_session import AuthSession
from app.models.email_token import (
    PURPOSE_EMAIL_VERIFY,
    PURPOSE_PASSWORD_RESET,
)
from app.models.user import User
from app.repositories.auth_session_repository import AuthSessionRepository
from app.repositories.email_token_repository import EmailTokenRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import SessionResponse, TokenResponse
from app.services.email_service import email_service

logger = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AuthService:
    """Handles all authentication business logic."""

    def __init__(self):
        self.user_repo = UserRepository()
        self.session_repo = AuthSessionRepository()
        self.email_token_repo = EmailTokenRepository()

    # ── Registration & login ────────────────────────────────────────────

    async def register(
        self,
        db: AsyncSession,
        *,
        email: str,
        password: str,
        full_name: str,
        phone: str = None,
        client: str = "web",
        device: Optional[str] = None,
        browser: Optional[str] = None,
        ip: Optional[str] = None,
        background_tasks: Optional[BackgroundTasks] = None,
    ) -> TokenResponse:
        """
        Register a new user, send a verification email, and return tokens.

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
        tokens = await self._create_session_and_tokens(
            db, user, client=client, device=device, browser=browser, ip=ip
        )
        if background_tasks is not None:
            raw = await self._issue_email_token(
                db,
                user_id=user.id,
                purpose=PURPOSE_EMAIL_VERIFY,
                ttl=timedelta(hours=settings.email_verification_expire_hours),
            )
            background_tasks.add_task(email_service.send_verification, user.email, raw)
        await db.commit()
        return tokens

    async def login(
        self,
        db: AsyncSession,
        *,
        email: str,
        password: str,
        client: str = "web",
        device: Optional[str] = None,
        browser: Optional[str] = None,
        ip: Optional[str] = None,
    ) -> TokenResponse:
        """
        Authenticate user and return tokens.

        Raises:
            InvalidCredentialsException: If email/password is wrong.
        """
        user = await self.user_repo.get_active_by_email(db, email)

        if not user or not verify_password(password, user.password_hash):
            raise InvalidCredentialsException()

        user.last_seen_at = _now()
        tokens = await self._create_session_and_tokens(
            db, user, client=client, device=device, browser=browser, ip=ip
        )
        await db.commit()
        return tokens

    # ── Refresh with rotation + reuse detection ─────────────────────────

    async def refresh(
        self,
        db: AsyncSession,
        *,
        raw_token: str,
        device: Optional[str] = None,
        browser: Optional[str] = None,
        ip: Optional[str] = None,
    ) -> tuple[TokenResponse, User]:
        """
        Rotate a refresh token: validate its session row, replace it with a
        child row, and return a fresh pair plus the authenticated user.

        Security behavior:
        - hash mismatch / revoked / expired → 401
        - replaced token presented again:
            · within `refresh_reuse_grace_seconds` of rotation → treated as a
              concurrent-refresh race: fork a new child (no revocation)
            · later → assume theft: revoke the WHOLE family + Redis marker
        - family older than `session_absolute_max_days` → revoke family
        """
        payload = decode_token(raw_token)
        if not payload or not verify_token_type(payload, "refresh"):
            raise InvalidTokenException()

        jti, sid, user_id = payload.get("jti"), payload.get("sid"), payload.get("sub")
        if not jti or not sid or not user_id:
            raise InvalidTokenException()

        row = await self.session_repo.get_by_id(db, uuid.UUID(jti))
        if row is None:
            raise InvalidTokenException()

        if hash_token(raw_token) != row.token_hash:
            logger.warning("refresh_token_hash_mismatch", jti=jti, user_id=user_id)
            raise InvalidTokenException()

        if row.revoked_at is not None:
            raise InvalidTokenException()

        if row.replaced_by is not None:
            # Reuse of a rotated token
            grace = timedelta(seconds=settings.refresh_reuse_grace_seconds)
            if row.last_used_at and _now() - row.last_used_at <= grace:
                # Concurrent-refresh race (multi tab / retry): fork, don't punish
                logger.info("refresh_race_fork", jti=jti, family=sid)
            else:
                await self.session_repo.revoke_family(db, row.family_id)
                await db.commit()
                await revoke_session_marker(str(row.family_id))
                logger.warning(
                    "token_reuse_detected",
                    family=str(row.family_id),
                    user_id=user_id,
                    ip=ip,
                )
                raise TokenReuseException()

        if row.expires_at <= _now():
            raise InvalidTokenException()

        # Absolute session age cap
        origin = await self.session_repo.get_family_origin(db, row.family_id)
        if origin and _now() - origin.created_at > timedelta(
            days=settings.session_absolute_max_days
        ):
            await self.session_repo.revoke_family(db, row.family_id)
            await db.commit()
            await revoke_session_marker(str(row.family_id))
            raise InvalidTokenException()

        user = await self.user_repo.get_active_by_id(db, user_id)
        if not user:
            raise InvalidTokenException()

        tokens = await self._create_session_and_tokens(
            db,
            user,
            client=row.client,
            device=device or row.device,
            browser=browser or row.browser,
            ip=ip or row.ip_address,
            family_id=row.family_id,
            rotated_from=row,
        )
        await db.commit()
        return tokens, user

    # ── Logout & revocation ──────────────────────────────────────────────

    async def logout(
        self,
        db: AsyncSession,
        *,
        raw_refresh: Optional[str] = None,
        access_payload: Optional[dict] = None,
    ) -> None:
        """
        Best-effort, idempotent logout: revoke the refresh family (if a valid
        refresh token was supplied) and denylist the current access token.
        Never raises.
        """
        family_to_mark: Optional[str] = None

        if raw_refresh:
            payload = decode_token(raw_refresh)
            if payload and verify_token_type(payload, "refresh") and payload.get("jti"):
                try:
                    row = await self.session_repo.get_by_id(
                        db, uuid.UUID(payload["jti"])
                    )
                    if row and hash_token(raw_refresh) == row.token_hash:
                        await self.session_repo.revoke_family(db, row.family_id)
                        await db.commit()
                        family_to_mark = str(row.family_id)
                except Exception as exc:  # noqa: BLE001 — logout must not fail
                    logger.warning("logout_refresh_revoke_failed", error=str(exc))

        if access_payload:
            sid = access_payload.get("sid")
            jti = access_payload.get("jti")
            exp = access_payload.get("exp")
            if sid:
                family_to_mark = family_to_mark or str(sid)
            if jti and exp:
                remaining = int(exp - _now().timestamp())
                await denylist_access_jti(jti, remaining)

            # A logged-out device must stop receiving pushes (shared devices).
            sub = access_payload.get("sub")
            if sub:
                try:
                    user = await self.user_repo.get_by_id(db, uuid.UUID(sub))
                    if user and user.fcm_token:
                        user.fcm_token = None
                        await db.commit()
                except Exception as exc:  # noqa: BLE001 — logout must not fail
                    logger.warning("logout_fcm_clear_failed", error=str(exc))

        if family_to_mark:
            await revoke_session_marker(family_to_mark)

    async def revoke_session(
        self, db: AsyncSession, *, user: User, family_id: uuid.UUID
    ) -> None:
        """Revoke one login session (family) owned by the user."""
        if not await self.session_repo.user_owns_family(db, user.id, family_id):
            raise NotFoundException(message="Session not found", code="SESSION_NOT_FOUND")
        await self.session_repo.revoke_family(db, family_id)
        await db.commit()
        await revoke_session_marker(str(family_id))

    async def revoke_all_sessions(
        self, db: AsyncSession, *, user: User, except_sid: Optional[str] = None
    ) -> int:
        """Revoke all the user's sessions except (optionally) the current one."""
        except_family = uuid.UUID(except_sid) if except_sid else None
        families = await self.session_repo.revoke_all_for_user(
            db, user.id, except_family=except_family
        )
        await db.commit()
        for fam in families:
            await revoke_session_marker(str(fam))
        return len(families)

    async def list_sessions(
        self, db: AsyncSession, *, user: User, current_sid: Optional[str]
    ) -> List[SessionResponse]:
        """Live login sessions for the settings UI."""
        tails = await self.session_repo.list_active_tails(db, user.id)
        return [
            SessionResponse(
                id=t.family_id,
                device=t.device,
                browser=t.browser,
                ip_address=t.ip_address,
                location=None,
                last_active=t.last_used_at or t.created_at,
                created_at=t.created_at,
                is_current=(current_sid is not None and str(t.family_id) == current_sid),
            )
            for t in tails
        ]

    # ── Email flows ──────────────────────────────────────────────────────

    async def forgot_password(
        self, db: AsyncSession, *, email: str, background_tasks: BackgroundTasks
    ) -> None:
        """
        Issue a reset token and email it — but ALWAYS behave identically
        whether or not the account exists (no user enumeration; the send
        happens after the response via BackgroundTasks).
        """
        user = await self.user_repo.get_active_by_email(db, email)
        if not user:
            return

        await self.email_token_repo.invalidate_outstanding(
            db, user.id, PURPOSE_PASSWORD_RESET
        )
        raw = await self._issue_email_token(
            db,
            user_id=user.id,
            purpose=PURPOSE_PASSWORD_RESET,
            ttl=timedelta(minutes=settings.password_reset_expire_minutes),
        )
        await db.commit()
        background_tasks.add_task(email_service.send_password_reset, user.email, raw)

    async def reset_password(
        self, db: AsyncSession, *, token: str, new_password: str
    ) -> None:
        """Consume a single-use reset token; revokes ALL sessions."""
        record = await self.email_token_repo.get_valid_by_hash(
            db, hash_token(token), PURPOSE_PASSWORD_RESET
        )
        if not record:
            raise InvalidTokenException()

        user = await self.user_repo.get_active_by_id(db, str(record.user_id))
        if not user:
            raise InvalidTokenException()

        user.password_hash = hash_password(new_password)
        # Proving control of the inbox also verifies the address
        user.email_verified = True
        record.used_at = _now()

        families = await self.session_repo.revoke_all_for_user(db, user.id)
        await db.commit()
        for fam in families:
            await revoke_session_marker(str(fam))
        logger.info("password_reset_completed", user_id=str(user.id))

    async def verify_email(self, db: AsyncSession, *, token: str) -> None:
        """Consume a single-use verification token."""
        record = await self.email_token_repo.get_valid_by_hash(
            db, hash_token(token), PURPOSE_EMAIL_VERIFY
        )
        if not record:
            raise InvalidTokenException()

        user = await self.user_repo.get_active_by_id(db, str(record.user_id))
        if not user:
            raise InvalidTokenException()

        user.email_verified = True
        record.used_at = _now()
        await db.commit()

    async def resend_verification(
        self, db: AsyncSession, *, user: User, background_tasks: BackgroundTasks
    ) -> None:
        if user.email_verified:
            return
        await self.email_token_repo.invalidate_outstanding(
            db, user.id, PURPOSE_EMAIL_VERIFY
        )
        raw = await self._issue_email_token(
            db,
            user_id=user.id,
            purpose=PURPOSE_EMAIL_VERIFY,
            ttl=timedelta(hours=settings.email_verification_expire_hours),
        )
        await db.commit()
        background_tasks.add_task(email_service.send_verification, user.email, raw)

    # ── Internals ────────────────────────────────────────────────────────

    async def _issue_email_token(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        purpose: str,
        ttl: timedelta,
    ) -> str:
        """Create a hashed single-use token row; returns the RAW token."""
        raw = secrets.token_urlsafe(32)
        await self.email_token_repo.create_token(
            db,
            user_id=user_id,
            purpose=purpose,
            token_hash=hash_token(raw),
            expires_at=_now() + ttl,
        )
        return raw


    async def _create_session_and_tokens(
        self,
        db: AsyncSession,
        user: User,
        *,
        client: str,
        device: Optional[str],
        browser: Optional[str],
        ip: Optional[str],
        family_id: Optional[uuid.UUID] = None,
        rotated_from: Optional[AuthSession] = None,
    ) -> TokenResponse:
        """
        Insert an auth_sessions row and mint the matching token pair.
        Does NOT commit — callers own the transaction.
        """
        token_id = uuid.uuid4()
        family = family_id or token_id

        refresh_jwt = create_refresh_token(str(user.id), str(family), str(token_id))
        access_jwt = create_access_token(str(user.id), str(family))

        row = AuthSession(
            id=token_id,
            family_id=family,
            user_id=user.id,
            token_hash=hash_token(refresh_jwt),
            client=client,
            device=device[:255] if device else None,
            browser=browser[:50] if browser else None,
            ip_address=ip[:45] if ip else None,
            expires_at=_now() + timedelta(days=settings.refresh_token_expire_days),
        )
        db.add(row)
        await db.flush()

        if rotated_from is not None:
            await self.session_repo.mark_replaced(db, rotated_from, token_id)

        return TokenResponse(
            access_token=access_jwt,
            refresh_token=refresh_jwt,
            expires_in=settings.access_token_expire_minutes * 60,
        )
