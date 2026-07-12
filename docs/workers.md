# Celery Workers & Scheduling

Everything slow or periodic runs through Celery: scraping, matching/alerts, CV processing, AI analysis/tailoring, cleanups.

## Celery app — [`app/workers/celery_app.py`](../app/workers/celery_app.py)

```
Celery("job_scout")
├── broker   = settings.redis_url            (redis db 0)
├── backend  = f"{settings.redis_url}/1"     (results, 1h expiry)
├── serialization: JSON only · timezone: UTC
├── task_time_limit 300s (soft 240s)
├── task_acks_late = True · worker_prefetch_multiplier = 1
├── worker_concurrency = 2 · default_retry_delay 60s · max_retries 3
└── autodiscovers tasks in app.workers
```

`acks_late` + prefetch 1 = a crashed worker re-delivers the in-flight task; tasks must therefore be idempotent (they are: job upsert dedups, alert creation is idempotent, CV processing overwrites).

## Queues

| Queue | Tasks | Why separate |
|---|---|---|
| `default` | `scrape_source`, `validate_job`, `notify_matching_users`, Beat periodic tasks | Normal traffic |
| `cv_processing` | `process_cv`, `analyze_cv_for_job`, `tailor_cv` | Slow (PDF + Gemini calls); isolates AI latency from the alert critical path |

Start a worker that consumes **all** of them — a worker without `-Q …,cv_processing` will leave every CV/AI task pending forever:

```bash
celery -A app.workers.celery_app worker -l info -Q default,scraping,notifications,cv_processing
```

(⚠️ `scraping`/`notifications` are declared in the compose command for future use; no task currently routes to them — [known issue #17](../../docs/known-issues.md).)

## Task catalog — [`app/workers/tasks.py`](../app/workers/tasks.py)

| Task | Queue | Retries | What it does |
|---|---|---|---|
| `scrape_source(source_id)` | default | 3 | Runs the source's registry scraper via [`scrape_service`](../app/services/scrape_service.py): fetch → dedup by `(source_id, external_id)` → structural sanity + cross-source dedup → insert/update jobs → source health + `ScrapeLog`. Fans out **`validate_job`** for each new alertable job (or `notify_matching_users` directly when `validation_enabled=False`) |
| `validate_job(job_id)` | default | 2 | Validates a new job's apply URL ([`validation_service`](../app/services/validation_service.py)): liveness + domain cross-check → persist `validation_status`. On `valid`/`unverified` fans out `notify_matching_users`; `dead`/`suspect` suppress the alert. Fail-open on flaky checks |
| `notify_matching_users(job_id)` | default | 3 | Speed-critical path: loads notifiable users, matches `user.preferences` (role keywords, company watchlist, location/remote), writes idempotent `UserJobAlert` rows, then sends ONE batched FCM push via [`app/core/push.py`](../app/core/push.py) — `is_delivered` set on success, dead tokens cleared; logged no-op without `FCM_CREDENTIALS_PATH` |
| `process_cv(user_id, cv_id)` | cv_processing | 2 | Downloads PDF from S3 → `pdfplumber` text → `_chunk_text` (2000 chars, 200 overlap, paragraph-aware) → `_detect_section` labels → Gemini embeddings (skipped without API key) → `CVChunk` rows → `extract_skills` against the shared taxonomy ([`app/core/skills.py`](../app/core/skills.py)) → upsert `user_skills` → status `ready`/`failed` |
| `backfill_job_skills(batch_size=500)` | default | — | One-off: populate `job_skills` for existing active jobs that have none (extraction runs at ingest going forward). Idempotent; run once to seed the recommendation feed |
| `analyze_cv_for_job(user_id, cv_id, job_id)` | cv_processing | 1 | ATS gap analysis via [`app/core/ai.py`](../app/core/ai.py); result cached in `cv_analyses` (24 h TTL) |
| `tailor_cv(user_id, cv_id, job_id)` | cv_processing | 1 | Rewrites CV summary/skills for the job (reuses cached `missing_keywords` when available); never cached — always fresh |
| `curate_cv(user_id, cv_id, job_id, draft_id)` | cv_processing | 1 | Full-CV curation: `parse_cv_structure` (cached on `user_cvs.parsed_structure`) → `tailor_cv_full` → draft to `review`. Documents are NOT generated here — user approves first |
| `generate_cv_document(draft_id)` | cv_processing | 2 | Renders an **approved** draft to DOCX+PDF ([`docgen.py`](../app/core/docgen.py)) → S3 `…/generated/{draft_id}/` → status `rendered` |

Also in `tasks.py`: `run_async()`, `_chunk_text`, `_detect_section`, `_extract_skills_from_text`, and the `SKILLS_TAXONOMY` dict (~10 categories of keyword→skill mappings used for non-AI skill extraction).

## Beat schedule — [`app/workers/scheduler.py`](../app/workers/scheduler.py)

| Beat entry | Task | Schedule |
|---|---|---|
| `check-scraper-health` | `check_scraper_health` | every 5 min (`crontab(minute="*/5")`) — flags failing sources ⚠️ alerting is print-only |
| `scrape-all-sources` | `scrape_all_active_sources` | every 15 min — loads due sources (`scrape_interval_minutes` elapsed) and fans out one `scrape_source` each |
| `revalidate-active-jobs` | `revalidate_active_jobs` | daily 02:00 UTC — re-checks apply-URL liveness for the oldest-validated active jobs; two dead readings in a row deactivate the listing |
| `cleanup-old-logs` | `cleanup_old_scrape_logs` | daily 03:00 UTC — deletes `scrape_logs` older than 30 days |
| `cleanup-expired-cv-analyses` | `cleanup_expired_cv_analyses` | daily 04:00 UTC — deletes `cv_analyses` past `expires_at` |

Run Beat separately: `celery -A app.workers.celery_app beat -l info`.

## run_async bridge

Celery tasks are sync; all services are async. Every task body is one line of orchestration:

```python
@celery_app.task(bind=True, max_retries=2, queue="cv_processing")
def process_cv(self, user_id: str, cv_id: str):
    return run_async(_process_cv(user_id, cv_id))   # new event loop per invocation
```

**Never** call async service code from a task without `run_async()` — both `tasks.py` and `scheduler.py` define/use it. Inside the async impl, open sessions with `async_session_maker()` (there's no request-scoped `get_db` here).

## Task status contract (client polling)

AI endpoints return a Celery `task_id`; clients poll `GET /users/me/cv/tasks/{task_id}`, which reads `AsyncResult` from the Redis result backend and maps to `CVTaskStatusResponse {status: pending|started|success|failure, result?}`. Results expire after 1 h. Task exceptions are logged in full but the polled `result` is **sanitized** — internal messages and API keys never reach clients.

## Monitoring

Flower runs in the root compose at http://localhost:5555 (task history, queue depth, worker state). For quick checks: `celery -A app.workers.celery_app inspect active` / `registered` / `stats`.
