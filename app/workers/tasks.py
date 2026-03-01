"""
Celery tasks for background processing.

ARCHITECTURE RULE: Same as routes — tasks are thin entry points.
They do exactly 3 things:
  1. Create a DB session (since we're outside FastAPI's request cycle)
  2. Call a service method  (or inline simple logic for CV processing)
  3. Return the result

NO raw SQL queries. NO business logic. NO model imports (except through services).
"""
import asyncio

from app.workers.celery_app import celery_app
from app.core.database import async_session_maker


def run_async(coro):
    """
    Helper to run async code in sync Celery tasks.

    Why is this needed? Celery workers are synchronous. Our services
    are async (because SQLAlchemy async requires it). This bridge
    creates an event loop, runs the coroutine, and cleans up.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(bind=True, max_retries=3)
def scrape_source(self, source_id: str):
    """
    Scrape a single job source and process results.

    This is the most important task in the system. The pipeline:
      Source -> Scraper -> Dedupe -> Save -> Notify

    Args:
        source_id: UUID of the job source to scrape
    """
    return run_async(_scrape_source(source_id))


async def _scrape_source(source_id: str):
    """Async implementation - delegates to ScrapeService."""
    from app.services.scrape_service import ScrapeService

    scrape_service = ScrapeService()

    async with async_session_maker() as db:
        result = await scrape_service.scrape_source(db, source_id)

        # If new jobs were found, trigger notifications immediately.
        # Why .delay() instead of calling directly? Because notification
        # for each job is independent work that can run in parallel
        # on different workers. This is the fan-out pattern.
        if result.get("new_job_ids"):
            for job_id in result["new_job_ids"]:
                notify_matching_users.delay(job_id)

        return result


@celery_app.task(bind=True, max_retries=3)
def notify_matching_users(self, job_id: str):
    """
    Find users matching a new job and send notifications.

    CRITICAL PATH: This fires immediately after a job is discovered.
    Speed here = competitive advantage. No batching, no delays.

    Args:
        job_id: UUID of the new job
    """
    return run_async(_notify_matching_users(job_id))


async def _notify_matching_users(job_id: str):
    """Async implementation - delegates to NotificationService."""
    from app.services.notification_service import NotificationService

    notification_service = NotificationService()

    async with async_session_maker() as db:
        return await notification_service.notify_for_new_job(db, job_id)


# ── CV Processing ─────────────────────────────────────────────────────────────

@celery_app.task(bind=True, max_retries=2, queue="cv_processing")
def process_cv(self, user_id: str, cv_id: str):
    """
    Process an uploaded CV: extract text with pdfplumber, match skills from taxonomy.

    Pipeline:
      1. Mark status = processing
      2. Stream PDF bytes from S3
      3. Extract text with pdfplumber
      4. Match skills against SKILLS_TAXONOMY keyword dict
      5. Replace UserSkill records for this CV
      6. Mark status = ready

    On failure, marks status = failed and retries up to max_retries times
    with exponential back-off.

    Args:
        user_id: UUID of the user (ownership check)
        cv_id:   UUID of the UserCV record
    """
    return run_async(_process_cv(user_id, cv_id))


async def _process_cv(user_id: str, cv_id: str):
    """Async implementation of CV processing."""
    import io
    import uuid as _uuid
    from datetime import datetime, timezone

    import pdfplumber
    from sqlalchemy import select, delete
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.core import storage
    from app.models.user_cv import (
        UserCV,
        UPLOAD_STATUS_PROCESSING,
        UPLOAD_STATUS_READY,
        UPLOAD_STATUS_FAILED,
    )
    from app.models.user_skill import UserSkill

    async with async_session_maker() as db:
        # ── 1. Fetch CV record ────────────────────────────────────────────────
        result = await db.execute(
            select(UserCV).where(
                UserCV.id == _uuid.UUID(cv_id),
                UserCV.user_id == _uuid.UUID(user_id),
                UserCV.is_active == True,
            )
        )
        cv = result.scalar_one_or_none()
        if not cv:
            return {"error": "CV not found", "cv_id": cv_id}

        cv.upload_status = UPLOAD_STATUS_PROCESSING
        await db.commit()

        try:
            # ── 2. Download PDF from S3 ───────────────────────────────────────
            pdf_bytes = await storage.download_bytes(cv.s3_key)

            # ── 3. Extract text via pdfplumber ────────────────────────────────
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                text = "\n".join(
                    page.extract_text() or "" for page in pdf.pages
                )

            text_lower = text.lower()

            # ── 4. Keyword skill matching ─────────────────────────────────────
            extracted = _extract_skills_from_text(text_lower)

            # ── 5. Upsert skills for this CV ──────────────────────────────────
            # Delete only the skills sourced from THIS cv (by cv_id).
            # Skills from other sources (manual, other CVs) are left untouched.
            await db.execute(
                delete(UserSkill).where(UserSkill.cv_id == cv.id)
            )
            # Use INSERT ... ON CONFLICT DO UPDATE (upsert) to handle the case
            # where the same skill already exists for this user from another
            # source — we update it to point at the current CV instead of
            # raising a unique-constraint violation.
            for skill_name, skill_category in extracted:
                stmt = (
                    pg_insert(UserSkill)
                    .values(
                        user_id=cv.user_id,
                        cv_id=cv.id,
                        skill_name=skill_name,
                        skill_category=skill_category,
                        source="cv",
                    )
                    .on_conflict_do_update(
                        constraint="uq_user_skill",
                        set_={
                            "cv_id": cv.id,
                            "skill_category": skill_category,
                            "source": "cv",
                        },
                    )
                )
                await db.execute(stmt)

            # ── 6. Mark ready ─────────────────────────────────────────────────
            cv.upload_status = UPLOAD_STATUS_READY
            cv.processed_at = datetime.now(timezone.utc)
            await db.commit()

            return {
                "cv_id": cv_id,
                "status": "ready",
                "skills_extracted": len(extracted),
            }

        except Exception as exc:  # noqa: BLE001
            cv.upload_status = UPLOAD_STATUS_FAILED
            await db.commit()
            # Re-raise so Celery can retry with exponential back-off
            raise exc


def _extract_skills_from_text(text_lower: str) -> list[tuple[str, str]]:
    """
    Simple keyword-in-text skill extraction against SKILLS_TAXONOMY.

    Returns a list of (skill_name, category) tuples for every taxonomy entry
    found in the CV text.  Tier 2 replaces this with vector similarity search.
    """
    found = []
    for category, skills in SKILLS_TAXONOMY.items():
        for skill in skills:
            if skill.lower() in text_lower:
                found.append((skill, category))
    return found


# ── Skills taxonomy ───────────────────────────────────────────────────────────
# category → list of canonical skill names (matching is case-insensitive substring search)
SKILLS_TAXONOMY: dict[str, list[str]] = {
    "languages": [
        "Python", "JavaScript", "TypeScript", "Java", "Kotlin", "Swift",
        "Go", "Rust", "C++", "C#", "C", "Ruby", "PHP", "Scala", "R",
        "Dart", "Elixir", "Haskell", "Clojure", "Lua", "Perl", "MATLAB",
        "Bash", "PowerShell", "SQL", "HTML", "CSS", "Sass", "SCSS",
    ],
    "frontend": [
        "React", "Vue", "Angular", "Next.js", "Nuxt", "Svelte", "SvelteKit",
        "Redux", "MobX", "Zustand", "Tailwind CSS", "Bootstrap", "Material UI",
        "Chakra UI", "Ant Design", "Storybook", "Webpack", "Vite", "Rollup",
        "Babel", "ESLint", "Prettier", "Jest", "Cypress", "Playwright",
        "Three.js", "D3.js", "Chart.js", "WebGL", "WebRTC", "WebSockets",
        "PWA", "Service Workers", "Web Components",
    ],
    "backend": [
        "FastAPI", "Django", "Flask", "Express", "NestJS", "Spring Boot",
        "Laravel", "Rails", "Gin", "Echo", "Fiber", "ASP.NET Core",
        "GraphQL", "REST", "gRPC", "OAuth2", "JWT",
        "OpenAPI", "Swagger", "Celery", "RabbitMQ", "Kafka", "SQS",
        "Bull", "BullMQ", "Dramatiq", "Pydantic", "SQLAlchemy", "Prisma",
        "TypeORM", "Sequelize", "Mongoose", "Hibernate", "GORM",
    ],
    "databases": [
        "PostgreSQL", "MySQL", "SQLite", "MariaDB", "Oracle",
        "MongoDB", "Redis", "Cassandra", "DynamoDB", "CosmosDB",
        "Elasticsearch", "OpenSearch", "InfluxDB", "TimescaleDB",
        "Neo4j", "Dgraph", "Fauna", "Supabase", "PlanetScale",
        "pgvector", "Pinecone", "Qdrant", "Weaviate", "Chroma", "Milvus",
        "Firestore",
    ],
    "cloud": [
        "AWS", "GCP", "Azure", "Cloudflare", "DigitalOcean", "Heroku",
        "Vercel", "Netlify", "Railway", "Render",
        "S3", "EC2", "Lambda", "ECS", "EKS", "Fargate", "CloudFront",
        "RDS", "Aurora", "SageMaker", "Bedrock",
        "Cloud Run", "Cloud Functions", "BigQuery",
        "App Service", "Azure Functions",
    ],
    "devops": [
        "Docker", "Kubernetes", "Helm", "Terraform", "Ansible", "Pulumi",
        "GitHub Actions", "GitLab CI", "CircleCI", "Jenkins", "ArgoCD",
        "Prometheus", "Grafana", "Datadog", "New Relic", "Sentry",
        "ELK Stack", "Kibana", "Logstash", "Fluentd", "OpenTelemetry",
        "Nginx", "Traefik", "Istio", "Envoy", "Linkerd",
        "Linux",
    ],
    "mobile": [
        "Flutter", "React Native", "SwiftUI",
        "Jetpack Compose", "Xamarin", "Ionic", "Capacitor",
        "Android SDK", "iOS SDK", "Expo", "Firebase",
    ],
    "ai_ml": [
        "Machine Learning", "Deep Learning", "NLP", "Computer Vision",
        "PyTorch", "TensorFlow", "Keras", "Scikit-learn", "XGBoost",
        "LangChain", "LlamaIndex", "OpenAI", "Anthropic", "Hugging Face",
        "Transformers", "BERT", "GPT", "RAG", "Vector Search", "Embeddings",
        "pandas", "NumPy", "Matplotlib", "Seaborn", "Plotly",
        "Jupyter", "MLflow",
    ],
    "testing": [
        "Unit Testing", "Integration Testing", "End-to-End Testing",
        "TDD", "BDD", "Pytest", "Jest", "Mocha", "Chai",
        "Playwright", "Cypress", "Selenium", "Postman",
        "k6", "Locust", "JMeter",
    ],
    "soft_skills": [
        "Agile", "Scrum", "Kanban", "JIRA", "Confluence",
        "Technical Writing", "Code Review", "Pair Programming",
        "Mentoring", "System Design",
    ],
}
