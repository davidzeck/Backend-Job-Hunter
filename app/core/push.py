"""
Firebase Cloud Messaging (FCM) wrapper.

All push sends go through this module so we can:
  - Lazily initialize the Firebase Admin SDK once, from FCM_CREDENTIALS_PATH
  - Run without credentials in development (logged no-op, like email_dev_mode)
  - Batch sends through messaging.send_each (500-message API limit)
  - Classify per-token outcomes so callers can clean up dead tokens

firebase-admin is a blocking SDK; the async entry point runs it in a worker
thread to keep the event loop free.
"""
import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# messaging.send_each hard limit per call.
_FCM_BATCH_LIMIT = 500

_app = None
_init_failed = False


class PushOutcome(str, Enum):
    SENT = "sent"              # accepted by FCM (relay, not human delivery)
    DEAD_TOKEN = "dead_token"  # UNREGISTERED — caller must delete the stored token
    RETRYABLE = "retryable"    # transient FCM-side failure
    FAILED = "failed"          # non-transient failure (bad payload, auth, ...)
    SKIPPED = "skipped"        # push not configured (dev mode)


@dataclass
class PushMessage:
    token: str
    title: str
    body: str
    # FCM requires data payload values to be strings.
    data: Dict[str, str] = field(default_factory=dict)


def push_configured() -> bool:
    """True when FCM credentials are configured (real sends possible)."""
    return bool(settings.fcm_credentials_path)


def _get_app():
    """Lazy singleton Firebase app. Returns None if init failed (logged once)."""
    global _app, _init_failed
    if _app is not None or _init_failed:
        return _app
    try:
        import firebase_admin
        from firebase_admin import credentials

        cred = credentials.Certificate(settings.fcm_credentials_path)
        _app = firebase_admin.initialize_app(cred)
        logger.info("fcm_initialized")
    except Exception as exc:  # noqa: BLE001 — a bad credentials file must not crash the worker
        _init_failed = True
        logger.error("fcm_init_failed", error=str(exc)[:300])
    return _app


def _classify_failure(exc: Optional[Exception]) -> PushOutcome:
    from firebase_admin import exceptions as fb_exceptions
    from firebase_admin import messaging

    if isinstance(exc, messaging.UnregisteredError):
        return PushOutcome.DEAD_TOKEN
    if isinstance(
        exc,
        (
            fb_exceptions.UnavailableError,
            fb_exceptions.InternalError,
            fb_exceptions.DeadlineExceededError,
            messaging.QuotaExceededError,
        ),
    ):
        return PushOutcome.RETRYABLE
    return PushOutcome.FAILED


def _send_batch_sync(batch: List[PushMessage]) -> List[PushOutcome]:
    """Blocking send of one ≤500-message batch. Never logs raw tokens."""
    from firebase_admin import messaging

    fcm_messages = [
        messaging.Message(
            token=m.token,
            notification=messaging.Notification(title=m.title, body=m.body),
            data=m.data,
            android=messaging.AndroidConfig(priority="high"),
        )
        for m in batch
    ]
    response = messaging.send_each(fcm_messages)

    outcomes: List[PushOutcome] = []
    for res in response.responses:
        if res.success:
            outcomes.append(PushOutcome.SENT)
        else:
            outcome = _classify_failure(res.exception)
            logger.warning(
                "fcm_send_failed",
                outcome=outcome.value,
                error=str(res.exception)[:200],
            )
            outcomes.append(outcome)
    return outcomes


async def send_push_messages(messages: List[PushMessage]) -> List[PushOutcome]:
    """
    Send a batch of push notifications. Returns one outcome per input message,
    in order. Never raises — a total transport failure maps to RETRYABLE.
    """
    if not messages:
        return []

    if not push_configured():
        # Dev without credentials: log what would have been sent.
        for m in messages:
            logger.info("push_dev_mode", title=m.title, body=m.body, data=m.data)
        return [PushOutcome.SKIPPED] * len(messages)

    if _get_app() is None:
        return [PushOutcome.FAILED] * len(messages)

    outcomes: List[PushOutcome] = []
    for i in range(0, len(messages), _FCM_BATCH_LIMIT):
        chunk = messages[i : i + _FCM_BATCH_LIMIT]
        try:
            outcomes.extend(await asyncio.to_thread(_send_batch_sync, chunk))
        except Exception as exc:  # noqa: BLE001 — transport error for the whole chunk
            logger.error("fcm_batch_failed", size=len(chunk), error=str(exc)[:300])
            outcomes.extend([PushOutcome.RETRYABLE] * len(chunk))
    return outcomes
