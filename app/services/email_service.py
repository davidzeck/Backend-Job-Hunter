"""
Outbound email via aiosmtplib.

Dev behavior: when SMTP credentials are not configured, the email is not
sent — the action link is logged instead (structlog), so local flows are
fully testable without a mail account.

Sends are enqueued with FastAPI BackgroundTasks (after the response), which
also keeps forgot-password timing identical for existing and unknown emails.
"""
from email.message import EmailMessage

import aiosmtplib

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class EmailService:
    """Thin async SMTP sender with dev-mode link logging."""

    @property
    def configured(self) -> bool:
        return bool(settings.smtp_user and settings.smtp_password)

    async def send(self, to: str, subject: str, html: str) -> None:
        if not self.configured:
            logger.info("email_dev_mode", to=to, subject=subject, body=html)
            return

        msg = EmailMessage()
        msg["From"] = settings.smtp_from_email
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content("This email requires an HTML-capable client.")
        msg.add_alternative(html, subtype="html")

        try:
            await aiosmtplib.send(
                msg,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_user,
                password=settings.smtp_password,
                start_tls=True,
                timeout=10,
            )
            logger.info("email_sent", to=to, subject=subject)
        except Exception as exc:  # noqa: BLE001 — background task must not crash the app
            logger.error("email_send_failed", to=to, subject=subject, error=str(exc))

    # ── Message builders ────────────────────────────────────────────────

    async def send_password_reset(self, to: str, raw_token: str) -> None:
        link = f"{settings.frontend_base_url}/reset-password?token={raw_token}"
        await self.send(
            to,
            "Reset your Job Scout password",
            f"""
            <p>Someone requested a password reset for your Job Scout account.</p>
            <p><a href="{link}">Reset your password</a>
               (valid for {settings.password_reset_expire_minutes} minutes).</p>
            <p>If this wasn't you, ignore this email — your password is unchanged.</p>
            """,
        )

    async def send_verification(self, to: str, raw_token: str) -> None:
        link = f"{settings.frontend_base_url}/verify-email?token={raw_token}"
        await self.send(
            to,
            "Verify your Job Scout email",
            f"""
            <p>Welcome to Job Scout!</p>
            <p><a href="{link}">Verify your email address</a>
               (valid for {settings.email_verification_expire_hours} hours).</p>
            """,
        )


email_service = EmailService()
