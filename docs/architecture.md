# Backend Architecture

## Layered design

Strict three-layer flow — each layer only talks to the one below it:

```
HTTP request
   │
   ▼
app/api/routes/*        thin controllers: parse, validate, auth, delegate
   │
   ▼
app/services/*          business logic, transactions, task enqueueing
   │
   ▼
app/repositories/*      all SQL queries (async SQLAlchemy)
   │
   ▼
app/models/*            ORM entities → PostgreSQL
```

- **Routes** ([`app/api/routes/`](../app/api/routes/)) never contain business logic. They resolve dependencies (DB session, current user, rate limit), call one service method, and shape the response with a Pydantic schema from [`app/schemas/`](../app/schemas/).
- **Services** ([`app/services/`](../app/services/)) own the rules: `auth_service`, `user_service`, `job_service`, `company_service`, `scrape_service`, `notification_service`, `alert_service`, `cv_service`. They compose repositories, external clients (S3, Gemini), and Celery.
- **Repositories** ([`app/repositories/`](../app/repositories/)) are the only place queries live: `base.py` (generic CRUD) + user/job/company/source/alert repositories.
- **Celery workers** ([`app/workers/`](../app/workers/)) reuse the *same services* — a task is just another entry point (see [workers.md](workers.md)).

## Application wiring — [`app/main.py`](../app/main.py)

Order matters; this is what a request passes through:

1. **Lifespan** (`lifespan` async context): startup runs `init_db()` (`create_all`) and logging setup; shutdown closes the engine.
2. **Rate limiter**: `app.state.limiter` (slowapi) + `RateLimitExceeded → 429` handler.
3. **`RequestIDMiddleware`** ([`app/core/logging.py`](../app/core/logging.py)): assigns/propagates `X-Request-ID`, binds it into structlog context.
4. **CORS middleware**: explicit origins from `settings.cors_origins`, explicit methods/headers (no wildcards).
5. **Exception handlers**: `APIException` subclasses → clean JSON with proper status; generic `Exception` → sanitized 500 (full detail only in logs).
6. **Router**: `app.include_router(api_router, prefix="/api/v1")` — the aggregate router from [`app/api/routes/__init__.py`](../app/api/routes/__init__.py).

## Dependency injection — [`app/api/deps.py`](../app/api/deps.py)

| Dependency | Yields | Notes |
|---|---|---|
| `get_db` | `AsyncSession` | From `async_session_maker` ([`app/core/database.py`](../app/core/database.py)); per-request session |
| `get_current_user` | `User` | HTTPBearer → decode JWT → verify `type == "access"` → load active user; 401 otherwise. Also sets `request.state.current_user` so the rate limiter can key by user id |
| `get_current_active_user` | `User` | + `is_active` check |
| `get_admin_user` | `User` | + `is_admin` check → 403 |
| `get_optional_user` | `User \| None` | For endpoints that behave differently when authenticated |

## Error handling — [`app/core/exceptions.py`](../app/core/exceptions.py)

Custom `APIException` hierarchy (`UnauthorizedException`, `InvalidTokenException`, `ForbiddenException`, `NotFoundException`, …) raised from services; the `main.py` handler maps them to status codes. Routes may also raise `HTTPException` directly for simple cases (404s on missing resources). Celery task errors are logged in full but returned to polling clients sanitized (see [security.md](security.md#error-sanitization)).

## Database access rules

- Engine/session: [`app/core/database.py`](../app/core/database.py) — async engine (asyncpg), pool size/overflow from settings, `async_session_maker`, `get_db` dependency, `init_db()`/`close_db()`.
- Models use `Mapped[...]`/`mapped_column` (SQLAlchemy 2.x style) and shared mixins from [`app/models/base.py`](../app/models/base.py): `UUIDMixin` (UUID PK) + `TimestampMixin` (`created_at`/`updated_at`).
- N+1 discipline: list endpoints aggregate with joins (e.g. `cv_service.list_cvs()` uses a single `outerjoin + group_by` for skill counts).
- Concurrency: guarded state transitions use `UPDATE … FOR UPDATE` instead of select-then-check (e.g. concurrent CV upload guard).

## External integrations

| Integration | Module | Pattern |
|---|---|---|
| S3 / MinIO | [`app/core/storage.py`](../app/core/storage.py) | aioboto3; endpoint URL switches MinIO↔AWS; presigned POST/GET |
| Google Gemini | [`app/core/ai.py`](../app/core/ai.py) | `google-genai` SDK; all inputs truncated; JSON responses defensively parsed; errors redacted |
| Redis | broker/results, slowapi storage, AI daily-cap counters | [`app/core/rate_limit.py`](../app/core/rate_limit.py) |
| FCM push | [`app/core/push.py`](../app/core/push.py) | firebase-admin; lazy init from `FCM_CREDENTIALS_PATH`; batched `send_each` via `asyncio.to_thread`; logged no-op without credentials (⚠️ Firebase project ops pending — [known issue #2b](../../docs/known-issues.md)) |
| SMTP | [`email_service.py`](../app/services/email_service.py) (auth emails only) | ⚠️ job-alert/digest emails not implemented — [known-issues](../../docs/known-issues.md) |

## Design decisions worth knowing

- **`init_db()` + Alembic both exist**: fresh environments need no migration step; existing databases evolve via Alembic. Keep both paths consistent ([database.md](database.md)).
- **Sync/async bridge**: Celery is sync; every task body is `run_async(_impl(...))` — one event loop per task invocation ([workers.md](workers.md#run_async-bridge)).
- **Cache-first AI**: analysis results live in `cv_analyses` with a 24 h TTL; the service returns the cache synchronously when fresh, else enqueues.
- **Fail-loud config**: [`app/core/config.py`](../app/core/config.py) `model_validator` rejects dev-default `secret_key`/S3 creds and missing `gemini_api_key` when `ENVIRONMENT` is `production`/`staging`.
