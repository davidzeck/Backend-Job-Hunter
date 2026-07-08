# API Reference

Base URL: `http://host:8000/api/v1` · Interactive docs: [`/docs`](http://localhost:8000/docs) (Swagger) and `/redoc`.

- **Auth**: 🔒 = `Authorization: Bearer <access_token>` required ([`get_current_user`](../app/api/deps.py)). 👑 = admin (`is_admin`). Open otherwise.
- **Rate limits** (slowapi, Redis-backed, keyed by user id or IP — [`app/core/rate_limit.py`](../app/core/rate_limit.py)): shown per endpoint; exceeding returns **429**.
- **Schemas** live in [`app/schemas/`](../app/schemas/). Paginated endpoints return `PaginatedResponse[T] = {items, total, page, limit, pages}`.
- **Errors**: JSON `{detail: string}` with conventional status codes (401 unauthenticated, 403 forbidden, 404 missing, 409 conflict, 422 validation, 429 rate-limited).

## Auth — [`routes/auth.py`](../app/api/routes/auth.py) (prefix `/auth`)

**Web/mobile split**: requests with the `X-Client: web` header get the refresh token as an httpOnly cookie (`jobscout_refresh`) and `refresh_token: null` in the body; without the header (Flutter, Swagger, scripts) tokens come in the body. Full model: [security.md](security.md).

| Method & path | Auth | Limit | Request | Response |
|---|---|---|---|---|
| `POST /auth/register` | — | 5/min | `RegisterRequest` JSON `{email, password(≥8), full_name?, phone?}` | **201** `TokenResponse {access_token, refresh_token?, token_type, expires_in}`; sends a verification email |
| `POST /auth/login` | — | 5/min | **Form** (OAuth2): `username`(=email), `password`, `remember_me?` | `TokenResponse`; web: cookie persists 7 d if `remember_me` else session cookie |
| `POST /auth/refresh` | — | 30/min | body `{refresh_token}` (mobile) **or** cookie + `X-Client: web` | `TokenResponse` — **rotates** the refresh token; reusing an old one revokes the whole session ([security.md](security.md#refresh-rotation--theft-detection)) |
| `POST /auth/logout` | optional Bearer | 30/min | optional body/cookie refresh token | `MessageResponse` — revokes the session + denylists the access token; always 200; web: clears cookie |
| `GET /auth/validate` | 🔒 | — | — | `{valid, user_id}` |

### Sessions

| Method & path | Auth | Request | Response |
|---|---|---|---|
| `GET /auth/sessions` | 🔒 | — | `list[SessionResponse]` (one per live login; `is_current` flag) |
| `DELETE /auth/sessions/{session_id}` | 🔒 | — | `MessageResponse`; 404 if not owned |
| `POST /auth/sessions/revoke-all` | 🔒 | — | `MessageResponse` (keeps the current session) |

### Email flows

| Method & path | Auth | Limit | Request | Response |
|---|---|---|---|---|
| `POST /auth/forgot-password` | — | 5/min | `{email}` | always the same 200 (no enumeration); emails a 60-min single-use link |
| `POST /auth/reset-password` | — | 5/min | `{token, new_password(≥8)}` | `MessageResponse`; revokes ALL sessions; 401 on invalid/expired/used token |
| `POST /auth/verify-email` | — | 5/min | `{token}` | `MessageResponse`; 401 on invalid token |
| `POST /auth/resend-verification` | 🔒 | 5/min | — | `MessageResponse` |

Access tokens live 30 min, refresh tokens 7 days ([config.py](../app/core/config.py)). Claims: `sub`, `sid` (session family), `jti`, `iat`, `exp`, `type` — tokens without `jti`/`sid` are rejected. Without SMTP credentials, emailed links are logged instead (`email_dev_mode` in the API logs).

## Users & profile — [`routes/users.py`](../app/api/routes/users.py) (prefix `/users`)

| Method & path | Auth | Limit | Request | Response |
|---|---|---|---|---|
| `GET /users/me` | 🔒 | — | — | `UserProfileResponse` (extends `UserResponse` with `has_cv`, `skills_count`) |
| `PATCH /users/me` | 🔒 | — | `UserUpdate` | `UserResponse` |
| `PUT /users/me/preferences` | 🔒 | — | `UserPreferences` (roles, companies, locations, push/email flags) | `UserResponse` |
| `PUT /users/me/fcm-token` | 🔒 | — | `UpdateFCMTokenRequest {fcm_token}` | `MessageResponse` |
| `POST /users/me/change-password` | 🔒 | — | `ChangePasswordRequest {current_password, new_password}` | `MessageResponse` — revokes all OTHER login sessions |
| `DELETE /users/me` | 🔒 | — | — | `MessageResponse` (account deletion) |

### Skills

| Method & path | Auth | Request | Response |
|---|---|---|---|
| `GET /users/me/skills` | 🔒 | — | `List[str]` (skill names) |
| `POST /users/me/skills` | 🔒 | `{skill_name}` (≤100 chars) | **201** `MessageResponse`; 400 if empty |
| `DELETE /users/me/skills/{skill_name}` | 🔒 | — | `MessageResponse`; 404 if not found |

### CV lifecycle (3-step upload)

| Method & path | Auth | Limit | Request | Response |
|---|---|---|---|---|
| `POST /users/me/cv/presign` | 🔒 | **3/hour** | `CVPresignRequest {filename, file_size_bytes, file_hash(sha256)}` | **201** `CVPresignResponse` (presigned POST url+fields, `cv_id`) |
| `POST /users/me/cv/{cv_id}/confirm` | 🔒 | — | `CVConfirmRequest` | `CVResponse` — verifies object exists, flips status, enqueues `process_cv` |
| `GET /users/me/cv` | 🔒 | — | — | `list[CVResponse]` (with skill counts, single joined query) |
| `GET /users/me/cv/{cv_id}/download-url` | 🔒 | — | — | `CVDownloadUrlResponse` (presigned GET, 1 h TTL) |
| `DELETE /users/me/cv/{cv_id}` | 🔒 | — | — | `MessageResponse` (soft-delete + S3 object delete) |

Upload rules: PDF only, ≤5 MB (enforced in presign *and* the S3 POST policy). Upload directly to S3/MinIO **without JWT headers**; policy fields must precede the file in the multipart body. Status machine: `pending_upload → uploaded → processing → ready | failed`. Full flow: [ai-cv-pipeline.md](ai-cv-pipeline.md).

### AI / ATS

| Method & path | Auth | Limit | Request | Response |
|---|---|---|---|---|
| `POST /users/me/cv/{cv_id}/analyze` | 🔒 | **10/hour + 50/day** | `CVAnalyzeRequest {job_id}` | `CVTaskStatusResponse` — cached `CVAnalysisResponse` inline if <24 h old, else `{task_id, status: pending}` |
| `POST /users/me/cv/{cv_id}/tailor` | 🔒 | **10/hour + 50/day** | `{job_id}` | `CVTaskStatusResponse {task_id}` — always async |
| `GET /users/me/cv/tasks/{task_id}` | 🔒 | 60/min | — | `CVTaskStatusResponse {status: pending\|started\|success\|failure, result?}` |

The daily cap returns 429 `"Daily AI usage limit reached. Try again tomorrow."` — it's a Redis counter separate from the hourly slowapi limit. Clients poll task status every ~2 s until terminal.

## Jobs — [`routes/jobs.py`](../app/api/routes/jobs.py) (prefix `/jobs`)

| Method & path | Auth | Request | Response |
|---|---|---|---|
| `GET /jobs/` | 🔒 | Query: `role`, `location`, `location_type`, `company` (repeatable), `days_ago`, `page`, `limit` | `PaginatedResponse[JobListItem]` |
| `GET /jobs/{job_id}` | 🔒 | — | `JobDetail` (adds description, seniority, salary, skills, timestamps) |
| `GET /jobs/{job_id}/skill-gap` | 🔒 | — | `SkillGapResponse {matched: SkillMatch[], missing: MissingSkill[], partial: PartialSkill[]}` — user skills vs job requirements |

## Companies — [`routes/companies.py`](../app/api/routes/companies.py) (prefix `/companies`)

Reads are user-level (Flutter uses them); writes are 👑 admin-only.

| Method & path | Auth | Request | Response |
|---|---|---|---|
| `GET /companies/` | 🔒 | — | `List[CompanyResponse]` |
| `POST /companies/` | 👑 | `CompanyCreate` | **201** `CompanyResponse`; **409** duplicate slug; **403** non-admin |
| `GET /companies/{company_id}` | 🔒 | — | `CompanyResponse`; 404 |
| `PATCH /companies/{company_id}` | 👑 | `CompanyUpdate` | `CompanyResponse`; 404 |
| `DELETE /companies/{company_id}` | 👑 | — | `MessageResponse`; 404 |

## Alerts — [`routes/alerts.py`](../app/api/routes/alerts.py) (prefix `/alerts`)

Alerts are `UserJobAlert` rows — the user's personalized job-match feed.

| Method & path | Auth | Request | Response |
|---|---|---|---|
| `GET /alerts/` | 🔒 | Query: `unread_only`, `page`, `limit` | `PaginatedResponse[AlertResponse]` |
| `PATCH /alerts/{alert_id}/read` | 🔒 | — | `MessageResponse` |
| `PATCH /alerts/{alert_id}/saved` | 🔒 | — | `MessageResponse` (toggle) |
| `PATCH /alerts/{alert_id}/applied` | 🔒 | — | `MessageResponse` |

## Sources — [`routes/sources.py`](../app/api/routes/sources.py) (prefix `/sources`)

Scraper-source administration (dashboard's Sources pages). 👑 **Admin-only at the router level** — every endpoint below returns 403 for non-admin users.

| Method & path | Auth | Request | Response |
|---|---|---|---|
| `GET /sources/` | 👑 | Query: search/company/type/status filters, sort, pagination | `PaginatedResponse[JobSourceResponse]` |
| `POST /sources/` | 👑 | `SourceCreate` (company_id, url, `scraper_class` registry key, interval) | **201** `JobSourceResponse` |
| `GET /sources/{source_id}` | 👑 | — | `JobSourceResponse`; 404 |
| `PATCH /sources/{source_id}` | 👑 | `SourceUpdate` | `JobSourceResponse`; 404 |
| `DELETE /sources/{source_id}` | 👑 | — | `MessageResponse`; 404 |
| `POST /sources/{source_id}/scrape` | 👑 | — | `{task_id, …}` — enqueues `scrape_source`; 404 if inactive/missing |
| `GET /sources/{source_id}/logs` | 👑 | pagination | `PaginatedResponse[ScrapeLogResponse]` |

## Admin — [`routes/admin.py`](../app/api/routes/admin.py) (prefix `/dashboard`)

| Method & path | Auth | Response |
|---|---|---|
| `GET /dashboard/stats` | 👑 | `DashboardStats` (totals for jobs/sources/alerts, powers the dashboard overview cards); 403 for non-admins |

## Health — [`routes/health.py`](../app/api/routes/health.py)

| Method & path | Auth | Response |
|---|---|---|
| `GET /health` | — | `HealthResponse` (includes a DB round-trip) |

## Endpoints clients call that DON'T exist (yet)

The dashboard UI still references these; they 404 against this backend — see [known issue #8](../../docs/known-issues.md): `/users/me/notifications*`, `/users/me/alert-preferences`, `/users/me/export`, `PATCH /jobs/{id}`, `GET /companies/{id}/jobs`, `GET /companies/{id}/sources`. (The auth/session/password-reset endpoints formerly on this list were implemented on 2026-07-08.)
