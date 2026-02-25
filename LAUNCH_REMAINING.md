# Job Scout Backend — Launch Remaining

_Stack: FastAPI · PostgreSQL · SQLAlchemy (async) · Celery + Redis · Alembic · Docker_
_Last updated: 2026-02-25_

---

## Current State

The backend architecture is solid and well-structured:
- Clean layered architecture (routes → services → repositories → models)
- Full route coverage: auth, jobs, alerts, users, companies, health
- Celery worker + Beat scheduler for scraping and notifications
- Base scrapers (StaticScraper, APIScraper) + 4 company scrapers (Greenhouse, Lever, Remotive, Safaricom)
- Docker Compose wired: api, worker, beat, PostgreSQL, Redis
- Pydantic v2 schemas, JWT auth, bcrypt password hashing

**The app cannot run today** — the database schema does not exist (no migrations have been generated).

---

## P0 — Blockers (app cannot function without these)

### 1. Generate and run Alembic migrations
`alembic/versions/` is empty. The database has no tables.

```bash
# One-time: generate initial migration from all models
alembic revision --autogenerate -m "initial_schema"
alembic upgrade head
```

- All models exist (`user`, `company`, `job`, `job_skill`, `job_source`, `scrape_log`, `user_cv`, `user_job_alert`, `user_skill`) — they just need their DDL generated.
- **Add** a `application_status` column to `user_job_alert` (`VARCHAR(20) DEFAULT 'Applied'`) for the mobile app's status tracking feature.

---

### 2. CV upload endpoint + file storage
The `UserCV` model exists. The `process_cv` Celery task exists (as a stub). But there is no API route to upload a CV.

**Required:**
```
POST /users/me/cv          → upload PDF, save to /uploads, enqueue process_cv task
GET  /users/me/cv          → return latest CV metadata
DELETE /users/me/cv/{id}   → delete a CV
```

Configure `python-multipart` (already in `requirements.txt`) + either local disk or S3/GCS via `boto3` / `google-cloud-storage`.

---

### 3. Implement CV processing task
`tasks.py::_process_cv` has a clear TODO block:

```python
# TODO: Implement actual processing:
# 1. Read PDF with pdfplumber          ← dependency already installed
# 2. Extract text
# 3. Match skills against taxonomy
# 4. Save UserSkill records
```

`pdfplumber` is already in `requirements.txt`. A skills taxonomy file (or database table) is needed. Even a basic keyword-match against 200–300 common tech skills would unlock the skill gap feature for real users.

---

### 4. Implement FCM push notifications
`notification_service.py::_send_push` is a print-only stub:

```python
async def _send_push(self, user: User, job: Job) -> bool:
    print(f"PUSH -> {user.email}: ...")   # ← never actually sends
    return True
```

`firebase-admin` is already in `requirements.txt`. Required steps:
1. Create a Firebase project, download service account JSON
2. Set `FIREBASE_CREDENTIALS_PATH` in `.env`
3. Implement actual `firebase_admin.messaging.send()` call with job title, company, and deep link URL

---

### 5. Password reset flow
`auth.py` has no forgot-password or reset-password routes. The Next.js dashboard has these forms built and waiting.

**Required endpoints:**
```
POST /auth/forgot-password   → send reset email with time-limited token
POST /auth/reset-password    → validate token, set new password
```

`aiosmtplib` is already in `requirements.txt`. Needs an email template and a `PasswordResetToken` model (or Redis TTL key).

---

### 6. Application status endpoint
The mobile app's `applied_screen.dart` now tracks `Applied → Interviewing → Offer → Rejected` per job. The backend has no endpoint for this.

**Required:**
```
PATCH /alerts/{alert_id}/status   body: { "status": "Interviewing" }
```

Maps to a new `application_status` column on `user_job_alert` (see item 1).

---

## P1 — Required for production quality

### 7. Job sources CRUD API
The admin dashboard has a full Sources UI but there are no API endpoints to manage sources.

**Required:**
```
GET    /sources/           → list all sources with last scrape status
POST   /sources/           → create a new source
PATCH  /sources/{id}       → update URL, frequency, config
DELETE /sources/{id}       → deactivate / remove
POST   /sources/{id}/scrape → manually trigger an immediate scrape
GET    /sources/{id}/logs   → paginated scrape logs for a source
```

---

### 8. Rate limiting
No throttling exists. Any unauthenticated or authenticated endpoint is unprotected against brute force.

Add `slowapi` (fastapi-compatible limiter):
```python
# Auth endpoints: 5 req/min per IP
# Job listing: 60 req/min per user
# Scrape trigger: 1 req/min per source
```

---

### 9. Token blacklist on logout
`POST /auth/logout` returns success without invalidating the JWT. On the server, the token remains valid until expiry.

Options (ascending complexity):
- **Redis TTL**: store `jti` (JWT ID) in Redis on logout, check on every request
- **Short-lived access tokens** (15 min) + long refresh tokens — already the architecture

At minimum: add `jti` claim to tokens and a Redis blocklist check in `get_current_user`.

---

### 10. Email verification on registration
New users are active immediately with no email verification. This enables spam registrations.

**Required:**
```
POST /auth/verify-email        → verify token sent in registration email
POST /auth/resend-verification → resend verification email
```

Add `email_verified_at` column (already exists in the `User` model as `email_verified: bool`). Block API access for unverified users (or limit to read-only).

---

### 11. Auth interceptor: concurrent refresh mutex
`auth_interceptor.dart` (mobile) has no mutex. Multiple parallel 401 responses can trigger multiple simultaneous refresh calls. This is also a backend concern: the refresh endpoint should be idempotent for the same `refresh_token` within a short window.

**Backend fix:** Add a per-refresh-token Redis lock (5s TTL) in `auth_service.refresh()` to make duplicate requests return the same token response.

---

### 12. Structured logging + Sentry
All errors are `print(f"Failed to notify user {user.id}: {e}")`. In production this is invisible.

```python
import logging
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.celery import CeleryIntegration
```

Add `python-json-logger` for structured JSON logs and Sentry for error tracking. Both are zero-config at the application level.

---

### 13. Database connection pooling + health check
The `/health` endpoint exists but does not check if the database is reachable.

**Required:**
```python
@router.get("/health/ready")
async def readiness(db: AsyncSession = Depends(get_db)):
    await db.execute(text("SELECT 1"))
    return {"db": "ok", "redis": await redis.ping()}
```

Set SQLAlchemy pool: `pool_size=10, max_overflow=20, pool_pre_ping=True`.

---

### 14. Test suite
`pytest` and `pytest-asyncio` are in `requirements.txt` but `tests/` does not exist.

**Minimum required tests before launch:**
- Auth: register, login, token refresh, duplicate email
- Job listing: pagination, filters, company slug filter
- Alert state transitions: mark read, toggle saved, mark applied
- Scrape service: deduplication logic (unit test with mock scraper)
- Notification matching: `_user_matches_job` preference logic

---

### 15. Seed data script
`scripts/seed.py` needs to:
1. Create initial companies (Safaricom, Equity, M-KOPA, Andela, Microsoft ADC, Google)
2. Create job sources for each company
3. Create a demo user with a known password
4. Insert sample jobs and alerts for that demo user

Without this, a fresh deployment has an empty database and nothing to show.

---

## P2 — Enterprise polish

### 16. Admin endpoints
No admin-scoped routes exist. The dashboard needs:

```
GET  /admin/users            → paginated user list
GET  /admin/users/{id}       → user detail with usage stats
POST /admin/users/{id}/deactivate
GET  /admin/stats            → aggregate stats for dashboard overview
GET  /admin/scrape-logs      → system-wide scrape log feed
```

Add `is_admin: bool` check in a `get_admin_user` dependency.

---

### 17. Scheduled scraping: source priority queue
Currently all sources are scraped at equal intervals. High-priority sources (Google, Microsoft) should scrape more frequently and get dedicated Celery queues.

```python
# celery beat schedule
CELERY_BEAT_SCHEDULE = {
    "scrape-high-priority": {
        "task": "app.workers.tasks.scrape_all_due",
        "schedule": crontab(minute="*/30"),
        "args": ["high"],
    },
    "scrape-normal": {
        "task": "app.workers.tasks.scrape_all_due",
        "schedule": crontab(minute="0", hour="*/2"),
        "args": ["normal"],
    },
}
```

---

### 18. Webhook / callback support
Allow external services (GitHub Actions, monitoring tools) to trigger a scrape for a specific source:

```
POST /webhooks/scrape/{source_id}
Authorization: Bearer {WEBHOOK_SECRET}
```

Useful for CI/CD: after deploying a new scraper, automatically trigger a test scrape.

---

### 19. Duplicate job deduplication (fuzzy)
Current deduplication checks `(source_id, external_id)` exactly. The same job posted on multiple boards (e.g., Greenhouse + LinkedIn) creates two records. Add a content hash or fuzzy title+company match as a secondary dedup layer.

---

### 20. CV storage: S3-compatible object storage
Local disk storage (`/uploads`) does not survive container restarts and doesn't scale horizontally.

Replace with:
- **AWS S3** or **Cloudflare R2** (S3-compatible, free egress)
- `boto3` for upload/download
- Signed URLs for CV downloads (time-limited, authenticated)

---

### 21. GDPR / data retention
- `DELETE /users/me` exists (soft-delete) but doesn't purge PII from alerts, CV records, etc.
- Add a hard-delete cascade job that runs after 30 days of soft-delete
- Add `GET /users/me/data-export` that returns all user data as JSON (GDPR Article 20)

---

## Infrastructure Checklist

| Item | Status |
|------|--------|
| Docker Compose (local) | ✓ Complete |
| Alembic setup | ✓ Configured, ✗ No migrations |
| PostgreSQL | ✓ In Compose |
| Redis | ✓ In Compose |
| Celery worker | ✓ In Compose |
| Celery Beat | ✓ In Compose |
| Production Dockerfile | ✓ Exists (review multi-stage) |
| Environment variables | ✓ `.env.example` exists |
| CI/CD pipeline | ✗ Missing (GitHub Actions) |
| Staging environment | ✗ Missing |
| SSL/TLS termination | ✗ Missing (nginx/Caddy needed) |
| Secrets management | ✗ Hardcoded in .env (use Vault/AWS SSM) |
| Backup strategy | ✗ Missing (pg_dump cron) |
| Monitoring (Sentry) | ✗ Missing |
| APM (Datadog/Grafana) | ✗ Missing |
| Horizontal scaling config | ✗ Missing (Kubernetes/ECS) |

---

## Quick-start order for first deploy

1. Generate Alembic migration → run `alembic upgrade head`
2. Run `scripts/seed.py` to populate demo data
3. Set up Firebase project → implement `_send_push`
4. Implement password reset endpoints + aiosmtplib email
5. Implement CV upload endpoint (local disk first, S3 later)
6. Add `slowapi` rate limiting to auth routes
7. Wire Sentry (`SENTRY_DSN` env var)
8. Write core test suite (auth + job listing + deduplication)
9. Set up GitHub Actions: lint → test → build Docker → deploy to staging
10. Configure nginx/Caddy for SSL + reverse proxy
