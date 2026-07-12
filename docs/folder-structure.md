# Backend Folder Structure

Annotated tree of `Job-backend/`. Rule of thumb: **routes are thin, services think, repositories query.**

```
Job-backend/
├── app/
│   ├── main.py                    # FastAPI app: lifespan, middleware, exception handlers, /api/v1 router
│   │
│   ├── api/
│   │   ├── deps.py                # DI: get_db, get_current_user, get_admin_user, get_optional_user
│   │   └── routes/                # THIN controllers only — one file per resource
│   │       ├── __init__.py        # api_router: aggregates + registers all routers
│   │       ├── auth.py            # /auth: register, login, refresh, logout
│   │       ├── users.py           # /users/me: profile, preferences, skills, CV lifecycle, AI analyze/tailor, task polling
│   │       ├── jobs.py            # /jobs: list (filters+pagination), detail, skill-gap
│   │       ├── companies.py       # /companies: CRUD
│   │       ├── sources.py         # /sources: scraper-source CRUD, manual scrape trigger, scrape logs
│   │       ├── alerts.py          # /alerts: list, mark read/saved/applied
│   │       ├── admin.py           # /dashboard/stats (admin)
│   │       └── health.py          # /health
│   │
│   ├── core/                      # Cross-cutting infrastructure (no business logic)
│   │   ├── config.py              # Pydantic Settings from env/.env; prod-secrets validator
│   │   ├── database.py            # async engine, async_session_maker, get_db, init_db/close_db
│   │   ├── security.py            # JWT create/decode, bcrypt hash/verify
│   │   ├── storage.py             # S3/MinIO via aioboto3: presign upload/download, key sharding, delete
│   │   ├── ai.py                  # Gemini: embeddings, JD keywords, ATS analysis, tailoring
│   │   ├── push.py                # FCM: lazy init, batched send_each, outcome classification
│   │   ├── skills.py              # SKILLS_TAXONOMY + extract_skills (shared: user_skills & job_skills)
│   │   ├── docgen.py              # CVStructure JSON → DOCX (python-docx) / PDF (fpdf2), ATS-safe template
│   │   ├── rate_limit.py          # slowapi limiter (Redis) + RATE_* constants + daily AI cap
│   │   ├── logging.py             # structlog setup, secret redaction, RequestIDMiddleware
│   │   └── exceptions.py          # APIException hierarchy
│   │
│   ├── models/                    # SQLAlchemy 2.x ORM (Mapped/mapped_column) — 11 tables
│   │   ├── base.py                # UUIDMixin, TimestampMixin, BaseModel
│   │   ├── user.py                # users
│   │   ├── company.py             # companies
│   │   ├── job_source.py          # job_sources (+ mark_success/mark_failure health helpers)
│   │   ├── job.py                 # jobs
│   │   ├── job_skill.py           # job_skills
│   │   ├── scrape_log.py          # scrape_logs
│   │   ├── user_job_alert.py      # user_job_alerts (match/alert records)
│   │   ├── user_cv.py             # user_cvs (+ upload-status state machine constants)
│   │   ├── user_skill.py          # user_skills (uq_user_skill unique constraint)
│   │   ├── cv_chunk.py            # cv_chunks (chunk text + 768-d embedding JSONB)
│   │   └── cv_analysis.py         # cv_analyses (24h-TTL ATS analysis cache)
│   │
│   ├── schemas/                   # Pydantic request/response models (API contract)
│   │   ├── base.py                # BaseSchema, PaginatedResponse[T], MessageResponse, ErrorResponse
│   │   ├── auth.py  user.py  job.py  company.py  source.py  alert.py
│   │   └── cv.py                  # CV + AI schemas (presign, confirm, analysis, tailor, task status)
│   │
│   ├── repositories/              # ALL SQL lives here
│   │   ├── base.py                # generic CRUD base
│   │   └── user_/job_/company_/source_/alert_repository.py
│   │
│   ├── services/                  # Business logic — the layer to read to understand behavior
│   │   ├── auth_service.py        # register/login/refresh flows
│   │   ├── user_service.py        # profile, preferences, skills
│   │   ├── job_service.py         # job queries, skill-gap computation
│   │   ├── company_service.py
│   │   ├── scrape_service.py      # ingestion pipeline: scrape → structural/dedup checks → persist → health → log
│   │   ├── validation_service.py  # job validation: apply-URL liveness + domain cross-check + staleness sweep
│   │   ├── notification_service.py# user↔job matching + alert creation + batched FCM send
│   │   ├── alert_service.py       # alert list/read/saved/applied
│   │   ├── cv_service.py          # CV lifecycle, analysis caching, tailoring orchestration
│   │   └── cv_draft_service.py    # curation drafts: curate → review/edit → approve → download
│   │
│   ├── scrapers/
│   │   ├── base.py                # BaseScraper (rate-limit, robots.txt, timing), StaticScraper, APIScraper
│   │   ├── registry.py            # SCRAPER_REGISTRY: scraper_class key → class (Strategy pattern)
│   │   └── companies/             # greenhouse.py, lever.py, remotive.py, safaricom.py
│   │
│   └── workers/
│       ├── celery_app.py          # Celery("job_scout"): Redis broker/results, serialization, limits
│       ├── tasks.py               # scrape_source, notify_matching_users, process_cv, analyze_cv_for_job,
│       │                          #   tailor_cv + run_async bridge + chunking + SKILLS_TAXONOMY
│       └── scheduler.py           # beat_schedule + periodic tasks (health check, scrape fan-out, cleanups)
│
├── alembic/
│   ├── env.py                     # async-aware migration env
│   └── versions/                  # 001 CV upload status/s3_key · 002 AI/ATS layer (cv_chunks, cv_analyses)
├── alembic.ini
├── scripts/seed.py                # dev seed data
├── docs/                          # ← you are here
├── Dockerfile
├── docker-compose.yml             # backend-only stack (db, redis, minio+bucket, api, worker, beat)
├── requirements.txt
├── .env / .env.example
└── ARCHITECTURE.md, LAUNCH_REMAINING.md, optimization.md, Cv-handling-Ai-consideration.md   # design notes
```

## Where to make common changes

| Change | Touch these |
|---|---|
| New endpoint | `app/api/routes/<resource>.py` → register in `routes/__init__.py` → schema in `app/schemas/` → logic in `app/services/` → queries in `app/repositories/` |
| New table / column | `app/models/` → `alembic revision --autogenerate -m "…"` → **review the migration** → keep `create_all` path consistent |
| New scraper | `app/scrapers/companies/<name>.py` → register in `registry.py` → add a `job_sources` row (see [scraping.md](scraping.md#adding-a-scraper)) |
| New background job | `app/workers/tasks.py` (use `run_async`; set `queue=` if not default) → schedule in `scheduler.py` if periodic |
| New setting | `app/core/config.py` + `.env.example`; add to `_DEV_ONLY_DEFAULTS` if secret |

⚠️ **There is no `tests/` directory** — pytest is installed but no tests exist yet ([known-issues](../../docs/known-issues.md)).
