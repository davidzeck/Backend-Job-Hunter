# Security

## Authentication — hybrid JWT + server-side sessions

Since 2026-07-08 auth uses **stateless access tokens + server-tracked rotating refresh tokens** (see `auth_sessions` in [database.md](database.md)). Implementation: [`app/core/security.py`](../app/core/security.py), [`app/services/auth_service.py`](../app/services/auth_service.py), [`app/api/deps.py`](../app/api/deps.py), [`app/core/denylist.py`](../app/core/denylist.py).

### Token model

```
Access  (30 min): { sub, sid, jti, iat, exp, type: "access" }   — stateless JWT (HS256)
Refresh (7 days): { sub, sid, jti, iat, exp, type: "refresh" }  — jti = auth_sessions.id
```

- `sid` = the session **family id**, stable across rotations — one login session = one family.
- Tokens without `jti`/`sid` (pre-hardening) are rejected outright.
- `decode_token` allows 10 s clock-skew leeway. Passwords: bcrypt (rounds=12).

### Refresh rotation & theft detection ([auth_service.refresh](../app/services/auth_service.py))

1. Every refresh **rotates**: a child `auth_sessions` row is created; the old row gets `replaced_by` + `last_used_at`. The raw token is never stored — only its sha256 (`token_hash`).
2. **Reuse detection**: presenting an already-replaced token >60 s after its rotation (`refresh_reuse_grace_seconds`) is treated as theft → the **whole family is revoked** + Redis marker + `token_reuse_detected` log. Within 60 s it's treated as a concurrent-refresh race (multi-tab) and forks instead.
3. Families have an absolute age cap: `session_absolute_max_days` (30 d).
4. Expired/revoked/hash-mismatched tokens → 401. Refresh validation is Postgres-backed and **fails closed**.

### Instant access-token revocation ([denylist.py](../app/core/denylist.py))

Redis markers checked in `get_current_user` on every request:
- `auth:revoked_sid:{sid}` — kills every outstanding access token of a session (TTL = access lifetime). Set on logout, session revoke, revoke-all, password change, password reset, refresh-reuse revocation.
- `auth:denylist:jti:{jti}` — a single access token (TTL = remaining life).

**Fail-open policy**: on Redis errors the check logs `denylist_check_failed` and allows the request — availability over a ≤30-min revocation window; the Postgres gate at refresh still fails closed.

### Web vs mobile transport

| | Web (dashboard) | Mobile (Flutter) / Swagger |
|---|---|---|
| Signal | `X-Client: web` header | no header |
| Refresh token | **httpOnly cookie** `jobscout_refresh` (`SameSite=Lax; Path=/`; `Secure` outside development; `Max-Age=7d` when remember-me, else session cookie); body `refresh_token: null` | in response body; sent back in the refresh body |
| Access token | JS memory only (Zustand, never persisted) | flutter_secure_storage |
| CSRF | SameSite=Lax + the cookie is only read when `X-Client: web` is present (forms can't send custom headers) | n/a (no cookies) |

The dashboard reaches the API through the **same-origin Next.js rewrite** (`/api/v1/*` → backend), so the cookie is first-party and no CORS-with-credentials is needed. uvicorn runs with `--proxy-headers` so rate limits and session IPs see real client addresses.

### Session management

`GET /auth/sessions` (device/browser/IP/last-active per family, `is_current`), `DELETE /auth/sessions/{family_id}`, `POST /auth/sessions/revoke-all` (spares current). Password **change** revokes all other sessions; password **reset** revokes all. Logout revokes the family and denylists the presented access token — verified idempotent.

### Email flows ([email_service.py](../app/services/email_service.py), `email_tokens` table)

Forgot/reset password and email verification use single-use tokens: `secrets.token_urlsafe(32)`, stored **hashed**, TTL 60 min (reset) / 24 h (verify). Forgot-password always returns the identical 200 and defers sending to `BackgroundTasks` (no user enumeration, timing-safe). Without SMTP credentials the link is logged instead of sent (`email_dev_mode`).

## Authorization (RBAC)

| Surface | Requirement |
|---|---|
| `/sources/*` (all) | 👑 admin — router-level `Depends(get_admin_user)` |
| `/companies` POST/PATCH/DELETE | 👑 admin (reads stay user-level — Flutter needs them) |
| `/dashboard/stats` | 👑 admin |
| Everything else authenticated | any active user |

First admin: `python -m scripts.create_admin email@example.com` (idempotent create-or-promote). The dashboard hides Sources/Companies nav and guards those routes for non-admins.

## Rate limiting — [`app/core/rate_limit.py`](../app/core/rate_limit.py)

slowapi, fixed-window, **Redis-backed**, keyed by user id (injected via `request.state.current_user` in `get_current_user`) with IP fallback for anonymous calls.

| Constant | Limit | Applied to |
|---|---|---|
| `RATE_AUTH` | 5/min | register, login, forgot/reset password, verify email, resend |
| `RATE_REFRESH` | 30/min | refresh, logout |
| `RATE_CV_UPLOAD` | 3/hour | CV presign |
| `RATE_AI` | 10/hour | analyze, tailor |
| `RATE_TASK_POLL` | 60/min | task status polling |
| Daily AI cap | 50/day/user | analyze + tailor (Redis `INCR` + 24 h TTL) |

## Configuration hardening — [`app/core/config.py`](../app/core/config.py)

`_reject_dev_secrets_in_production`: `production`/`staging` boot **fails** on dev-default `secret_key`/S3 creds or missing `gemini_api_key`. Auth knobs: `refresh_cookie_name`, `refresh_reuse_grace_seconds`, `session_absolute_max_days`, `password_reset_expire_minutes`, `email_verification_expire_hours`, `frontend_base_url` (email links), `cookie_secure` property.

## Logging & error hygiene

- structlog with `_redact_secrets` (Gemini/AWS key patterns) + `RequestIDMiddleware` correlation ([logging.py](../app/core/logging.py)).
- Security events logged: `token_reuse_detected`, `refresh_token_hash_mismatch`, `denylist_*_failed`, `password_reset_completed`, `email_dev_mode`.
- Generic 500s are opaque; Celery task errors reach polling clients sanitized; AI errors pass `_sanitize_error` ([ai.py](../app/core/ai.py)).

## Input validation & S3 model

Pydantic on every body (register password ≥8, skill ≤100 chars, UUID checks). CV upload constraints pinned server-side in the presigned POST policy (`application/pdf`, ≤5 MB, 15-min window). Clients never hold storage credentials; **no JWT headers on S3 requests**. TOCTOU-sensitive transitions use `FOR UPDATE` guards ([cv_service.py](../app/services/cv_service.py)).

## Tests

Auth is covered by the backend's first test suite — 43 tests in [`Job-backend/tests/`](../tests/): login contract (form + JSON guard), cookie mode, remember-me, rotation, grace-fork vs family revocation, absolute age cap, forged tokens, logout/denylist, legacy-token rejection, sessions endpoints, password change/reset revocation, RBAC, email flows, per-user rate keying. Run: `pytest tests -q` (needs Postgres + Redis; see [tests/conftest.py](../tests/conftest.py)).

## Threat-model quick reference

| Threat | Mitigation |
|---|---|
| Credential stuffing | 5/min auth limit + bcrypt(12) |
| Stolen refresh token | rotation + reuse detection revokes the family; sessions listable/revocable |
| Stolen access token | 30-min TTL + sid/jti denylist on any revocation event |
| XSS token theft (web) | refresh token httpOnly; access token memory-only, never persisted |
| CSRF on cookie refresh | SameSite=Lax + required `X-Client` custom header |
| Password-change race | all other sessions revoked; reset revokes all |
| User enumeration | identical, timing-safe forgot-password responses |
| S3 / LLM cost abuse | presign 3/hr, policy-pinned uploads; AI 10/hr + 50/day, input truncation |
| Secret leakage | structlog redaction, sanitized errors, fail-loud prod config |
| Redis outage | denylist fails open (logged, bounded by 30-min TTL); refresh fails closed at Postgres |
