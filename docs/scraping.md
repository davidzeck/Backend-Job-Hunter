# Job Scraping & Ingestion

How jobs get into the system: Beat wakes every 15 min → due `job_sources` are scraped by their registered scraper class → results are deduped and persisted → new jobs trigger user matching.

## Scraper class hierarchy — [`app/scrapers/base.py`](../app/scrapers/base.py)

```
BaseScraper (ABC)
├── rate limiting (settings.scrape_rate_limit_per_minute)
├── user-agent rotation (settings.scrape_user_agent base)
├── robots.txt check
├── execute() — timing/error wrapper → ScrapeResult
│
├── StaticScraper   # HTML pages: httpx + BeautifulSoup/lxml
└── APIScraper      # JSON endpoints: httpx
```

Data contracts (dataclasses in `base.py`): `ScrapedJob` (one normalized job posting) and `ScrapeResult` (jobs + stats + errors from one run).

## Registry — [`app/scrapers/registry.py`](../app/scrapers/registry.py)

Strategy pattern: each `job_sources` row stores a `scraper_class` string key; `get_scraper(key)` instantiates the class.

| Registry key | Class | File | Type |
|---|---|---|---|
| `greenhouse` | `GreenhouseAPIScraper` | [companies/greenhouse.py](../app/scrapers/companies/greenhouse.py) | APIScraper — works for any Greenhouse-hosted board |
| `lever` | `LeverAPIScraper` | [companies/lever.py](../app/scrapers/companies/lever.py) | APIScraper — any Lever-hosted board |
| `remotive` | `RemotiveAPIScraper` | [companies/remotive.py](../app/scrapers/companies/remotive.py) | APIScraper — Remotive job API |
| `safaricom_careers` | `SafaricomCareersScraper` | [companies/safaricom.py](../app/scrapers/companies/safaricom.py) | Site-specific scraper |

Per-source knobs live in `job_sources.config` (JSONB) — e.g. board tokens/company identifiers consumed by the scraper.

## Ingestion pipeline — [`app/services/scrape_service.py`](../app/services/scrape_service.py)

```mermaid
flowchart TD
    A[scrape_source task] --> B[load JobSource]
    B --> C["get_scraper(source.scraper_class)"]
    C --> D["scraper.execute() → ScrapeResult"]
    D --> E{for each ScrapedJob:<br/>exists by source_id + external_id?}
    E -- yes --> F[update if description changed]
    E -- no --> G[insert Job → collect new_job_ids]
    F & G --> H["source.mark_success() / mark_failure()<br/>(health_status, consecutive_failures)"]
    H --> I[write ScrapeLog<br/>jobs_found / new / updated / duration / errors]
    I --> J[fan out notify_matching_users per new job]
```

Trigger paths:
- **Scheduled**: Beat `scrape_all_active_sources` (every 15 min) → `get_due_sources()` (sources whose `scrape_interval_minutes` has elapsed) → one `scrape_source` task each.
- **Manual**: `POST /api/v1/sources/{source_id}/scrape` (dashboard "Scrape now") → same task.

## Matching & alerting — [`app/services/notification_service.py`](../app/services/notification_service.py)

`notify_for_new_job(job_id)`:
1. `UserRepository.get_notifiable_users()` — active users with push enabled.
2. `_user_matches_job()` filters on `user.preferences`: role keywords vs job title, company watchlist (by slug), location match including remote.
3. Idempotent `UserJobAlert` insert per match.
4. `_send_push()` — ⚠️ **stub**: prints and returns `True`; real FCM via `firebase-admin` is pending ([known issue #2](../../docs/known-issues.md)).

## Source health

`job_sources` tracks `health_status`, `last_success_at`, `consecutive_failures`; `mark_success()`/`mark_failure()` are called by the pipeline. Beat's `check_scraper_health` (every 5 min) inspects these — ⚠️ but its admin alerting is also print-only.

## Adding a scraper

1. Create `app/scrapers/companies/<name>.py` subclassing `APIScraper` (JSON) or `StaticScraper` (HTML). Implement the fetch/parse to yield `ScrapedJob`s — look at [greenhouse.py](../app/scrapers/companies/greenhouse.py) as the cleanest template.
2. Register it in [`registry.py`](../app/scrapers/registry.py): add `"<key>": YourScraper` to `SCRAPER_REGISTRY`.
3. Create the source row: `POST /api/v1/sources/` with `scraper_class: "<key>"`, the target URL, `scrape_interval_minutes`, and any `config` the scraper needs (or add it to [`scripts/seed.py`](../scripts/seed.py)).
4. Test manually: `POST /api/v1/sources/{id}/scrape`, then check `GET /sources/{id}/logs` and the `jobs` table.

Etiquette guardrails already built in: per-source rate limiting, timeout (`scrape_timeout_seconds`), robots.txt respect, and an identifying user agent — don't bypass them in new scrapers.

## Stable dedup requirement

`external_id` must be **stable per job per source** (ATS job id, not list position). Dedup is `(source_id, external_id)`; a scraper with unstable ids floods the system with duplicate "new" jobs — and each one fans out user alerts.
