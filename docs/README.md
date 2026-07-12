# Job Scout Backend — Documentation

FastAPI + async SQLAlchemy 2.x + Celery + PostgreSQL + Redis + MinIO/S3 + Google Gemini. This is the **active** backend for Job Scout (the folder name `Job-backend` is historical; there is no other backend).

System-level context (all components, flows, infra): [`../../docs/`](../../docs/README.md).

## Docs in this folder

| Doc | What it covers |
|---|---|
| [architecture.md](architecture.md) | Layered design (routes → services → repositories), request lifecycle, DI, errors |
| [folder-structure.md](folder-structure.md) | Annotated tree of `app/` — where everything lives |
| [api-reference.md](api-reference.md) | Every endpoint: method, path, auth, rate limit, schemas |
| [database.md](database.md) | All 11 tables, ERD, migrations, data conventions |
| [workers.md](workers.md) | Celery config, task catalog, queues, Beat schedule, `run_async()` bridge |
| [scraping.md](scraping.md) | Scraper base classes, registry, ingestion pipeline, adding a scraper |
| [ai-cv-pipeline.md](ai-cv-pipeline.md) | CV upload → processing → embeddings → ATS analysis → tailoring |
| [security.md](security.md) | JWT auth, rate limits, config validation, logging/redaction, CORS |

## Quick start

```bash
cd Job-backend

# 1. Infra (Postgres + Redis + MinIO with bucket auto-created)
docker compose up -d db redis minio createbuckets

# 2. Python env
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # defaults work against the compose infra

# 3. API — note the module path: app.main:app
uvicorn app.main:app --reload
# → http://localhost:8000/docs (Swagger) / /redoc

# 4. Celery worker — MUST name the queues, or CV/AI tasks sit unprocessed
celery -A app.workers.celery_app worker -l info -Q default,scraping,notifications,cv_processing

# 5. Celery beat (periodic scraping + cleanups)
celery -A app.workers.celery_app beat -l info

# Optional: seed data
python scripts/seed.py
```

A fresh database self-provisions: `init_db()` runs `Base.metadata.create_all` at startup ([app/core/database.py](../app/core/database.py)). For schema changes on an existing DB use Alembic — see [database.md](database.md#migrations).

Everything under Docker instead: `docker compose up -d` from this folder (or the repo root for the full stack incl. dashboard + Flower).

## Orientation in 60 seconds

- HTTP surface: 8 routers under `/api/v1` — see [api-reference.md](api-reference.md).
- Business logic lives in [`app/services/`](../app/services/); routes are thin; queries live in [`app/repositories/`](../app/repositories/).
- Heavy work (scraping, CV processing, AI) is Celery — [workers.md](workers.md).
- Config is env-driven Pydantic settings ([`app/core/config.py`](../app/core/config.py)); production refuses dev defaults.
- Auth: rotating refresh-token sessions + Redis revocation — read [security.md](security.md) before touching anything auth-adjacent. Tests: `pytest tests -q` (43 auth tests; needs Postgres + Redis).
- ⚠️ Known gaps (push needs Firebase project ops, health alerting is a stub, tests cover auth + push only): [`../../docs/known-issues.md`](../../docs/known-issues.md). Launch checklist: [`../LAUNCH_REMAINING.md`](../LAUNCH_REMAINING.md).

## Conventions (enforce these in PRs)

- New endpoint → route in [`app/api/routes/`](../app/api/routes/) + logic in a service + queries in a repository. No business logic in route handlers.
- Async SQLAlchemy only: `mapped_column`, `async_session_maker`. Sync code (Celery) reaches async services through `run_async()`.
- Upserts on unique-constrained tables (e.g. `user_skills`) use `pg_insert().on_conflict_do_update()`.
- Errors: raise `HTTPException`/`APIException` with correct status codes; never leak internals (Celery errors are sanitized before hitting clients).
- Long-running work: enqueue a Celery task and return a `task_id` the client polls.
