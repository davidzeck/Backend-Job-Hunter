# Job Scout Backend Architecture

> Enterprise job alert platform for software engineers in Kenya/East Africa

## Executive Summary

Job Scout's competitive advantage is **speed to notification** — being the first to alert users when matching jobs appear. This architecture prioritizes:

1. **Sub-minute alert latency** from job discovery to push notification
2. **Reliable scraping** with graceful degradation and self-healing
3. **Horizontal scalability** to handle growth across East Africa
4. **Cost efficiency** appropriate for an emerging market startup

---

## 1. System Overview

### 1.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              EDGE LAYER                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                          │
│  │ Cloudflare  │  │   Mobile    │  │  Dashboard  │                          │
│  │  WAF/CDN    │  │  (Flutter)  │  │  (Next.js)  │                          │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘                          │
└─────────┼────────────────┼────────────────┼─────────────────────────────────┘
          │                │                │
          ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           API GATEWAY                                        │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                    Nginx / Traefik                                    │   │
│  │         • TLS Termination  • Rate Limiting  • Load Balancing         │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         APPLICATION TIER                                     │
│                                                                              │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐                 │
│  │   FastAPI #1   │  │   FastAPI #2   │  │   FastAPI #N   │                 │
│  │   (Uvicorn)    │  │   (Uvicorn)    │  │   (Uvicorn)    │                 │
│  └───────┬────────┘  └───────┬────────┘  └───────┬────────┘                 │
│          │                   │                   │                           │
│          └───────────────────┼───────────────────┘                           │
│                              │                                               │
└──────────────────────────────┼───────────────────────────────────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
          ▼                    ▼                    ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│      Redis       │  │   PostgreSQL     │  │  Firebase FCM    │
│   Cache/Broker   │  │    (Primary)     │  │  Push Service    │
└──────────────────┘  └──────────────────┘  └──────────────────┘
          │                    │
          │                    ▼
          │           ┌──────────────────┐
          │           │   PostgreSQL     │
          │           │   (Read Replica) │
          │           └──────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          WORKER TIER                                         │
│                                                                              │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐                 │
│  │ Celery Worker  │  │ Celery Worker  │  │  Celery Beat   │                 │
│  │  (Scraping)    │  │ (Notifications)│  │  (Scheduler)   │                 │
│  └────────────────┘  └────────────────┘  └────────────────┘                 │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Component Responsibilities

| Component | Responsibility | Scaling Strategy |
|-----------|---------------|------------------|
| **API Servers** | HTTP request handling, authentication, data validation | Horizontal (add instances) |
| **Scraper Workers** | Fetch jobs from career pages | Horizontal (add workers) |
| **Notification Workers** | Match jobs to users, send push | Horizontal (dedicated queue) |
| **Redis** | Session cache, Celery broker, rate limiting | Vertical then cluster |
| **PostgreSQL** | Persistent data storage | Read replicas for queries |
| **Firebase FCM** | Push notification delivery | Managed service |

---

## 2. Application Architecture

### 2.1 Layered Architecture Pattern

We follow a **four-layer architecture** to separate concerns and enable testability:

```
┌─────────────────────────────────────────────────────────────┐
│                      API LAYER (routes/)                     │
│  • HTTP request/response handling                            │
│  • Input validation via Pydantic                             │
│  • Authentication via dependencies                           │
│  • NO business logic                                         │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   SERVICE LAYER (services/)                  │
│  • Business logic and orchestration                          │
│  • Cross-domain operations                                   │
│  • Transaction boundaries                                    │
│  • External service integration                              │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                 REPOSITORY LAYER (repositories/)             │
│  • Database queries (read/write)                             │
│  • Data access abstraction                                   │
│  • Query optimization                                        │
│  • NO business logic                                         │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    DATA LAYER (models/, schemas/)            │
│  • SQLAlchemy ORM models                                     │
│  • Pydantic validation schemas                               │
│  • Database migrations (Alembic)                             │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Current vs Target Structure

**Current (MVP):**
```
app/
├── api/routes/          # Routes with inline logic (acceptable for MVP)
├── core/                # Config, security, exceptions
├── models/              # SQLAlchemy models
├── schemas/             # Pydantic schemas
├── scrapers/            # Scraping infrastructure
└── workers/             # Celery tasks
```

**Target (Enterprise):**
```
app/
├── api/
│   ├── v1/              # Versioned API
│   │   ├── endpoints/   # Route handlers only
│   │   └── router.py    # Combined router
│   └── deps.py          # Shared dependencies
├── core/
│   ├── config.py
│   ├── security.py
│   ├── logging.py       # Structured logging
│   └── exceptions.py
├── services/            # Business logic layer
│   ├── job_service.py
│   ├── user_service.py
│   ├── notification_service.py
│   └── matching_service.py
├── repositories/        # Data access layer
│   ├── base.py
│   ├── job_repository.py
│   ├── user_repository.py
│   └── alert_repository.py
├── models/              # Database models
├── schemas/             # Request/Response DTOs
├── scrapers/            # Scraping subsystem
├── workers/             # Background tasks
└── integrations/        # External services
    ├── firebase.py
    ├── smtp.py
    └── sms.py           # Africa's Talking for SMS
```

---

## 3. Data Architecture

### 3.1 Entity Relationship Diagram

```
┌──────────────────┐       ┌──────────────────┐       ┌──────────────────┐
│      User        │       │     Company      │       │    JobSource     │
├──────────────────┤       ├──────────────────┤       ├──────────────────┤
│ id (PK, UUID)    │       │ id (PK, UUID)    │       │ id (PK, UUID)    │
│ email            │       │ name             │◄──────┤ company_id (FK)  │
│ hashed_password  │       │ slug             │       │ scraper_class    │
│ full_name        │       │ careers_url      │       │ source_url       │
│ preferences (J)  │       │ logo_url         │       │ config (JSONB)   │
│ fcm_token        │       │ is_active        │       │ health_status    │
│ is_active        │       └────────┬─────────┘       │ last_scraped_at  │
└────────┬─────────┘                │                 └────────┬─────────┘
         │                          │                          │
         │                          ▼                          │
         │                 ┌──────────────────┐                │
         │                 │       Job        │                │
         │                 ├──────────────────┤                │
         │                 │ id (PK, UUID)    │◄───────────────┘
         │                 │ company_id (FK)  │
         │                 │ source_id (FK)   │
         │                 │ external_id      │  ◄── Unique per source
         │                 │ title            │
         │                 │ description      │
         │                 │ location         │
         │                 │ location_type    │
         │                 │ apply_url        │
         │                 │ discovered_at    │
         │                 │ is_active        │
         │                 └────────┬─────────┘
         │                          │
         │    ┌─────────────────────┼─────────────────────┐
         │    │                     │                     │
         │    ▼                     ▼                     ▼
         │ ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐
         │ │  JobSkill    │  │  ScrapeLog   │  │  UserJobAlert    │
         │ ├──────────────┤  ├──────────────┤  ├──────────────────┤
         │ │ job_id (FK)  │  │ source_id    │  │ user_id (FK)     │◄───┐
         │ │ skill_name   │  │ status       │  │ job_id (FK)      │    │
         │ │ is_required  │  │ jobs_found   │  │ notified_at      │    │
         │ │ min_years    │  │ new_jobs     │  │ is_read          │    │
         │ └──────────────┘  │ duration_ms  │  │ is_saved         │    │
         │                   │ error_message│  │ is_applied       │    │
         │                   └──────────────┘  └──────────────────┘    │
         │                                                              │
         └──────────────────────────────────────────────────────────────┘

┌──────────────────┐       ┌──────────────────┐
│    UserSkill     │       │     UserCV       │
├──────────────────┤       ├──────────────────┤
│ user_id (FK)     │       │ user_id (FK)     │
│ skill_name       │       │ file_path        │
│ proficiency      │       │ original_name    │
│ years_experience │       │ processed_at     │
└──────────────────┘       │ extracted_skills │
                           └──────────────────┘
```

### 3.2 Database Strategy

**Connection Pooling:**
```python
# For production, use PgBouncer as middleware
# Local config for development:
engine = create_async_engine(
    DATABASE_URL,
    pool_size=5,           # Base connections
    max_overflow=10,       # Burst capacity
    pool_timeout=30,       # Wait for connection
    pool_recycle=1800,     # Recycle every 30min
)
```

**Read/Write Splitting (Future):**
```python
# Service layer routes queries appropriately
class JobService:
    def __init__(self, read_db: AsyncSession, write_db: AsyncSession):
        self.read_db = read_db    # Points to read replica
        self.write_db = write_db  # Points to primary

    async def get_jobs(self, filters):
        # Read operations go to replica
        return await self.job_repo.find_many(self.read_db, filters)

    async def create_job(self, data):
        # Write operations go to primary
        return await self.job_repo.create(self.write_db, data)
```

### 3.3 Key Indexes

```sql
-- Critical for job discovery speed
CREATE INDEX idx_jobs_discovered_at ON jobs(discovered_at DESC);
CREATE INDEX idx_jobs_company_active ON jobs(company_id, is_active);
CREATE INDEX idx_jobs_source_external ON jobs(source_id, external_id);

-- Critical for notification matching
CREATE INDEX idx_users_active_fcm ON users(is_active) WHERE fcm_token IS NOT NULL;
CREATE INDEX idx_alerts_user_unread ON user_job_alerts(user_id, is_read);

-- Critical for scraper health
CREATE INDEX idx_sources_health ON job_sources(health_status, is_active);
CREATE INDEX idx_sources_due ON job_sources(last_scraped_at, is_active);
```

---

## 4. Scraping Subsystem

### 4.1 Scraper Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         SCRAPING PIPELINE                                    │
│                                                                              │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐  │
│  │   Celery    │    │   Scraper   │    │    Job      │    │ Notification│  │
│  │    Beat     │───▶│   Worker    │───▶│  Processor  │───▶│   Trigger   │  │
│  │ (Scheduler) │    │  (Execute)  │    │ (Dedupe)    │    │  (Match)    │  │
│  └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘  │
│        │                   │                  │                  │          │
│        │                   │                  │                  │          │
│        ▼                   ▼                  ▼                  ▼          │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         PostgreSQL                                   │   │
│  │  job_sources │ jobs │ scrape_logs │ user_job_alerts                 │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Scraper Class Hierarchy

```python
# Base classes provide common functionality
BaseScraper
├── StaticScraper      # For HTML pages (BeautifulSoup)
│   ├── SafaricomCareersScraper
│   ├── EquityBankCareersScraper
│   └── KCBCareersScraper
│
└── APIScraper         # For JSON APIs
    ├── LinkedInJobsScraper
    ├── IndeedKenyaScraper
    └── BrighterMondayScraper
```

### 4.3 Rate Limiting & Compliance

```python
class BaseScraper:
    # Respectful scraping configuration
    REQUESTS_PER_MINUTE = 10
    MIN_DELAY_SECONDS = 6

    async def execute(self):
        # 1. Check robots.txt compliance
        if not await self.check_robots_txt():
            return ScrapeResult(success=False, error="Blocked by robots.txt")

        # 2. Rotate user agents
        headers = {"User-Agent": self._get_random_user_agent()}

        # 3. Add delays between requests
        await self.respectful_delay()

        # 4. Execute with timeout
        return await self.scrape()
```

### 4.4 Health Monitoring

```python
class JobSource(BaseModel):
    health_status: str  # 'healthy', 'degraded', 'failing'
    consecutive_failures: int
    last_error: str

    def mark_success(self, jobs_found: int, new_jobs: int):
        self.health_status = "healthy"
        self.consecutive_failures = 0
        self.last_scraped_at = datetime.now(timezone.utc)

    def mark_failure(self, error: str):
        self.consecutive_failures += 1
        self.last_error = error

        if self.consecutive_failures >= 3:
            self.health_status = "failing"
        elif self.consecutive_failures >= 1:
            self.health_status = "degraded"
```

---

## 5. Notification System

### 5.1 Notification Flow (Critical Path)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    JOB DISCOVERED → USER NOTIFIED                         │
│                                                                           │
│  Time: 0s              5s              10s             15s                │
│    │                    │               │               │                 │
│    ▼                    ▼               ▼               ▼                 │
│  ┌─────┐            ┌─────┐         ┌─────┐        ┌─────────┐           │
│  │Scrape│───────────▶│Save │────────▶│Match│───────▶│ Send    │           │
│  │ Job │            │ Job │         │Users│        │ Push    │           │
│  └─────┘            └─────┘         └─────┘        └─────────┘           │
│                                                                           │
│  Target: < 30 seconds from discovery to notification                      │
│                                                                           │
└──────────────────────────────────────────────────────────────────────────┘
```

### 5.2 User Matching Algorithm

```python
def _user_matches_job(user: User, job: Job) -> bool:
    """
    Fast matching based on user preferences.

    Preferences structure:
    {
        "companies": ["safaricom", "google-kenya"],
        "roles": ["backend_engineer", "fullstack"],
        "locations": ["nairobi", "remote"],
        "notifications": {"push": true, "email": false}
    }
    """
    prefs = user.preferences or {}

    # Quick exits (most selective filters first)
    if not prefs.get("notifications", {}).get("push", True):
        return False

    # Company filter
    companies = prefs.get("companies", [])
    if companies and job.company.slug not in companies:
        return False

    # Role filter (keyword matching)
    roles = prefs.get("roles", [])
    if roles:
        title_lower = job.title.lower()
        if not any(role.replace("_", " ") in title_lower for role in roles):
            return False

    # Location filter
    locations = prefs.get("locations", [])
    if locations:
        job_loc = (job.location or "").lower()
        if not any(loc in job_loc for loc in locations):
            if not ("remote" in locations and job.location_type == "remote"):
                return False

    return True
```

### 5.3 Push Notification Integration

```python
# integrations/firebase.py
import firebase_admin
from firebase_admin import messaging

class FirebasePushService:
    async def send_job_alert(self, user: User, job: Job) -> bool:
        message = messaging.Message(
            notification=messaging.Notification(
                title=f"New {job.title} at {job.company.name}",
                body=f"{job.location} • {job.location_type}",
            ),
            data={
                "job_id": str(job.id),
                "type": "new_job",
                "click_action": "OPEN_JOB_DETAIL",
            },
            token=user.fcm_token,
            android=messaging.AndroidConfig(
                priority="high",  # Wake device immediately
                ttl=timedelta(hours=1),
            ),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        alert=messaging.ApsAlert(
                            title=f"New {job.title}",
                            body=f"at {job.company.name}",
                        ),
                        sound="default",
                        badge=1,
                    ),
                ),
            ),
        )

        response = messaging.send(message)
        return response is not None
```

---

## 6. Security Architecture

### 6.1 Authentication Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         JWT AUTHENTICATION FLOW                              │
│                                                                              │
│  ┌────────┐         ┌────────┐         ┌────────┐         ┌────────┐       │
│  │ Client │         │  API   │         │  Auth  │         │   DB   │       │
│  └───┬────┘         └───┬────┘         └───┬────┘         └───┬────┘       │
│      │                  │                  │                  │             │
│      │  POST /login     │                  │                  │             │
│      │─────────────────▶│                  │                  │             │
│      │                  │  verify password │                  │             │
│      │                  │─────────────────▶│                  │             │
│      │                  │                  │  fetch user      │             │
│      │                  │                  │─────────────────▶│             │
│      │                  │                  │◀─────────────────│             │
│      │                  │◀─────────────────│                  │             │
│      │  {access_token,  │                  │                  │             │
│      │   refresh_token} │                  │                  │             │
│      │◀─────────────────│                  │                  │             │
│      │                  │                  │                  │             │
│      │  GET /jobs       │                  │                  │             │
│      │  Authorization:  │                  │                  │             │
│      │  Bearer <token>  │                  │                  │             │
│      │─────────────────▶│                  │                  │             │
│      │                  │  decode + verify │                  │             │
│      │                  │─────────────────▶│                  │             │
│      │                  │◀─────────────────│                  │             │
│      │                  │                  │                  │             │
│      │  {jobs: [...]}   │                  │                  │             │
│      │◀─────────────────│                  │                  │             │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 6.2 Token Strategy

| Token Type | Expiry | Storage | Purpose |
|------------|--------|---------|---------|
| **Access Token** | 30 minutes | Memory only | API authentication |
| **Refresh Token** | 7 days | Secure storage | Obtain new access token |

### 6.3 Security Checklist

- [x] Password hashing with bcrypt (cost factor 12)
- [x] JWT with RS256 or HS256 algorithm
- [x] HTTPS enforced at gateway
- [x] CORS restricted to known origins
- [x] Rate limiting per user/IP
- [ ] SQL injection prevention (parameterized queries)
- [ ] Input validation on all endpoints
- [ ] Secrets in environment variables (not .env in production)

---

## 7. Observability

### 7.1 Logging Strategy

```python
# core/logging.py
import structlog

def setup_logging():
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),  # JSON for production
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )

# Usage in code
logger = structlog.get_logger()
logger.info(
    "job_scraped",
    source_id=str(source.id),
    jobs_found=len(jobs),
    duration_ms=duration,
)
```

### 7.2 Key Metrics to Track

| Metric | Type | Alert Threshold |
|--------|------|-----------------|
| `scrape_duration_seconds` | Histogram | p95 > 30s |
| `scrape_jobs_found` | Gauge | 0 for 3 consecutive runs |
| `notification_latency_seconds` | Histogram | p95 > 60s |
| `api_request_duration_seconds` | Histogram | p95 > 500ms |
| `active_users_daily` | Gauge | — |
| `jobs_discovered_daily` | Counter | — |

### 7.3 Health Check Endpoint

```python
@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    checks = {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": {}
    }

    # Database check
    try:
        await db.execute(text("SELECT 1"))
        checks["checks"]["database"] = "ok"
    except Exception as e:
        checks["checks"]["database"] = f"error: {e}"
        checks["status"] = "unhealthy"

    # Redis check
    try:
        redis = aioredis.from_url(settings.redis_url)
        await redis.ping()
        checks["checks"]["redis"] = "ok"
    except Exception as e:
        checks["checks"]["redis"] = f"error: {e}"
        checks["status"] = "unhealthy"

    # Scraper health
    failing = await db.execute(
        select(func.count(JobSource.id))
        .where(JobSource.health_status == "failing")
    )
    failing_count = failing.scalar()
    checks["checks"]["scrapers_failing"] = failing_count

    status_code = 200 if checks["status"] == "healthy" else 503
    return JSONResponse(checks, status_code=status_code)
```

---

## 8. Deployment Architecture

### 8.1 Container Strategy

```yaml
# docker-compose.yml (Development)
services:
  api:
    build: .
    command: uvicorn app.main:app --host 0.0.0.0 --reload
    volumes:
      - .:/app
    depends_on:
      - db
      - redis

  worker:
    build: .
    command: celery -A app.workers worker -l info -c 2
    depends_on:
      - db
      - redis

  beat:
    build: .
    command: celery -A app.workers beat -l info
    depends_on:
      - redis

  db:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: jobscout
      POSTGRES_PASSWORD: password

  redis:
    image: redis:7-alpine
```

### 8.2 Production Deployment (Railway/Render)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          PRODUCTION SETUP                                    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                         Railway / Render                             │    │
│  │                                                                      │    │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │    │
│  │  │  API Service │  │Worker Service│  │ Beat Service │              │    │
│  │  │  (2 replicas)│  │  (2 workers) │  │  (1 replica) │              │    │
│  │  └──────────────┘  └──────────────┘  └──────────────┘              │    │
│  │                                                                      │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                              │                                               │
│                              ▼                                               │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    Managed Services                                  │    │
│  │                                                                      │    │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │    │
│  │  │   Neon DB    │  │  Upstash     │  │   Firebase   │              │    │
│  │  │ (PostgreSQL) │  │   (Redis)    │  │    (FCM)     │              │    │
│  │  └──────────────┘  └──────────────┘  └──────────────┘              │    │
│  │                                                                      │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 8.3 Environment Configuration

```bash
# Production environment variables (via secrets manager)
APP_NAME="Job Scout API"
DEBUG=false

# Database (connection pooling via managed service)
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/jobscout?sslmode=require

# Security (rotate every 90 days)
SECRET_KEY=<256-bit-random-key>

# Redis (Upstash serverless)
REDIS_URL=rediss://default:pass@host:6379

# Firebase
GOOGLE_APPLICATION_CREDENTIALS=/secrets/firebase.json

# Monitoring
SENTRY_DSN=https://xxx@sentry.io/xxx
```

---

## 9. Scaling Roadmap

### Phase 1: MVP (Current)
- Single API instance
- Single worker
- Basic scraping (5 companies)
- Push notifications only

### Phase 2: Growth (1,000+ users)
- Load-balanced API (2+ instances)
- Dedicated scraper workers
- Read replica for queries
- Email notifications
- SMS via Africa's Talking

### Phase 3: Scale (10,000+ users)
- Kubernetes deployment
- Auto-scaling based on queue depth
- Geographic distribution (Kenya, Uganda, Tanzania)
- ML-powered job matching
- Premium subscription tier

---

## 10. API Versioning Strategy

```python
# Versioned API structure
app/api/
├── v1/
│   ├── endpoints/
│   │   ├── jobs.py
│   │   ├── users.py
│   │   └── alerts.py
│   └── router.py
└── v2/                    # Future version
    └── ...

# main.py
app.include_router(api_v1_router, prefix="/api/v1")
# app.include_router(api_v2_router, prefix="/api/v2")  # When ready
```

**Versioning Rules:**
1. Breaking changes require new version
2. Deprecate old versions with 6-month notice
3. Support maximum 2 versions simultaneously
4. Version in URL, not headers (mobile app friendliness)

---

## Appendix A: Technology Choices

| Category | Choice | Rationale |
|----------|--------|-----------|
| **Framework** | FastAPI | Async, type hints, auto docs |
| **ORM** | SQLAlchemy 2.0 | Async support, mature ecosystem |
| **Database** | PostgreSQL | JSONB, reliability, free tiers |
| **Cache/Broker** | Redis | Celery support, pub/sub for real-time |
| **Task Queue** | Celery | Battle-tested, scheduling, retries |
| **Push** | Firebase FCM | Free tier, cross-platform |
| **Hosting** | Railway/Render | Simple, affordable, Africa-friendly |

---

## Appendix B: Local Development

```bash
# 1. Clone and setup
git clone <repo>
cd Job-backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Environment
cp .env.example .env
# Edit .env with local values

# 3. Database
docker-compose up -d db redis
alembic upgrade head

# 4. Run services (3 terminals)
uvicorn app.main:app --reload          # API
celery -A app.workers worker -l info   # Worker
celery -A app.workers beat -l info     # Scheduler

# 5. Test
pytest tests/ -v
```

---

*Last Updated: February 2026*
*Version: 1.0.0*
