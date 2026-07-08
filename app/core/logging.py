"""
Structured logging configuration using structlog.

- Production: JSON lines (machine-readable, compatible with log aggregators)
- Development: Pretty-printed console output with colors
- Secret redaction: API keys and tokens are stripped from ALL log output
"""
import logging
import re
import sys
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.config import settings

# Patterns that must never appear in log output.
_SECRET_RE = re.compile(
    r"(AIza[0-9A-Za-z_-]{35}|"       # Google / Gemini API key
    r"sk-[a-zA-Z0-9]{20,}|"          # OpenAI-style key
    r"AKIA[0-9A-Z]{16}|"             # AWS access key ID
    r"(?<=[=: \"])[A-Za-z0-9/+=]{40,}(?=[\" ,}\n])|"  # generic long secrets
    r"key[=:]\\s*\\S+)",              # key=value in error messages
    re.IGNORECASE,
)


def _redact_secrets(logger, method_name, event_dict):
    """Structlog processor: scrub API keys from every log value."""
    for key, value in event_dict.items():
        if isinstance(value, str) and _SECRET_RE.search(value):
            event_dict[key] = _SECRET_RE.sub("[REDACTED]", value)
    return event_dict


def setup_logging() -> None:
    """Configure structlog and stdlib logging. Call once at startup."""
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        _redact_secrets,
    ]

    if settings.environment == "development":
        renderer = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if settings.debug else logging.INFO)

    # Silence noisy third-party loggers
    for noisy in ("uvicorn.access", "httpx", "aiobotocore",
                  "botocore", "boto3", "s3transfer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a named structlog logger."""
    return structlog.get_logger(name)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Inject a unique request ID into every request for log correlation."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
