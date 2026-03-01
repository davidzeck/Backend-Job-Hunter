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
from app.core.logging import get_logger

logger = get_logger(__name__)


def run_async(coro):
    """
    Helper to run async code in sync Celery tasks.

    Celery workers are synchronous. Our services are async (because
    SQLAlchemy async requires it). This bridge creates an event loop,
    runs the coroutine, and cleans up.
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

    Pipeline: Source -> Scraper -> Dedupe -> Save -> Notify
    """
    return run_async(_scrape_source(source_id))


async def _scrape_source(source_id: str):
    """Async implementation - delegates to ScrapeService."""
    from app.services.scrape_service import ScrapeService

    scrape_service = ScrapeService()

    async with async_session_maker() as db:
        result = await scrape_service.scrape_source(db, source_id)

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
    Process an uploaded CV: extract text, chunk, embed, match skills.

    Pipeline:
      1. Mark status = processing
      2. Stream PDF bytes from S3
      3. Extract text with pdfplumber
      4. Store full_text on CV record
      5. Chunk text into overlapping segments
      6. Generate embeddings (if OpenAI key set)
      7. Store CVChunk rows
      8. Match skills against SKILLS_TAXONOMY
      9. Upsert UserSkill records
      10. Mark status = ready
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
    from app.core.config import settings
    from app.models.user_cv import (
        UserCV,
        UPLOAD_STATUS_PROCESSING,
        UPLOAD_STATUS_READY,
        UPLOAD_STATUS_FAILED,
    )
    from app.models.user_skill import UserSkill
    from app.models.cv_chunk import CVChunk

    async with async_session_maker() as db:
        # ── 1. Fetch CV record ──────────────────────────────────────────────
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
            # ── 2. Download PDF from S3 ─────────────────────────────────────
            pdf_bytes = await storage.download_bytes(cv.s3_key)

            # ── 3. Extract text via pdfplumber ──────────────────────────────
            try:
                with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                    text = "\n".join(
                        page.extract_text() or "" for page in pdf.pages
                    )
            except Exception as exc:
                logger.error("pdf_parse_failed", cv_id=cv_id, error=str(exc))
                raise RuntimeError("Failed to extract text from PDF") from exc

            if not text.strip():
                logger.warning("pdf_empty_text", cv_id=cv_id)
                cv.upload_status = UPLOAD_STATUS_FAILED
                await db.commit()
                return {"error": "PDF contains no extractable text", "cv_id": cv_id}

            # ── 4. Store full text on CV record ─────────────────────────────
            cv.full_text = text

            # ── 5. Chunk text ───────────────────────────────────────────────
            chunks = _chunk_text(text, max_chars=2000, overlap_chars=200)

            # ── 6. Generate embeddings (skip if no API key) ─────────────────
            embeddings = None
            if settings.openai_api_key:
                try:
                    from app.core.ai import generate_embeddings_batch
                    embeddings = await generate_embeddings_batch(
                        [c["text"] for c in chunks]
                    )
                    logger.info("embeddings_generated", cv_id=cv_id, count=len(embeddings))
                except Exception as exc:
                    # Non-fatal: store chunks without embeddings
                    logger.warning(
                        "embedding_generation_failed",
                        cv_id=cv_id,
                        error=str(exc),
                    )
                    embeddings = None
            else:
                logger.info("embeddings_skipped_no_api_key", cv_id=cv_id)

            # ── 7. Store CVChunk rows (idempotent: delete old first) ────────
            await db.execute(delete(CVChunk).where(CVChunk.cv_id == cv.id))

            for i, chunk in enumerate(chunks):
                db.add(CVChunk(
                    cv_id=cv.id,
                    user_id=cv.user_id,
                    chunk_index=i,
                    chunk_text=chunk["text"],
                    embedding=embeddings[i] if embeddings and i < len(embeddings) else None,
                    section_label=chunk.get("section"),
                ))

            # ── 8. Keyword skill matching ───────────────────────────────────
            text_lower = text.lower()
            extracted = _extract_skills_from_text(text_lower)

            # ── 9. Upsert skills for this CV ────────────────────────────────
            await db.execute(
                delete(UserSkill).where(UserSkill.cv_id == cv.id)
            )
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

            # ── 10. Mark ready ──────────────────────────────────────────────
            cv.upload_status = UPLOAD_STATUS_READY
            cv.processed_at = datetime.now(timezone.utc)
            await db.commit()

            logger.info(
                "cv_processed",
                cv_id=cv_id,
                skills=len(extracted),
                chunks=len(chunks),
                has_embeddings=embeddings is not None,
            )

            return {
                "cv_id": cv_id,
                "status": "ready",
                "skills_extracted": len(extracted),
                "chunks_created": len(chunks),
            }

        except RuntimeError:
            # Already sanitized error messages — re-raise for Celery retry
            cv.upload_status = UPLOAD_STATUS_FAILED
            await db.commit()
            raise
        except Exception as exc:
            cv.upload_status = UPLOAD_STATUS_FAILED
            await db.commit()
            logger.error("cv_processing_failed", cv_id=cv_id, exc_info=True)
            raise RuntimeError("Unexpected error during CV processing") from exc


# ── CV Analysis Task ──────────────────────────────────────────────────────────


@celery_app.task(bind=True, max_retries=1, queue="cv_processing")
def analyze_cv_for_job(self, user_id: str, cv_id: str, job_id: str):
    """Analyze a CV against a job description. Returns analysis result dict."""
    try:
        return run_async(_analyze_cv_for_job(user_id, cv_id, job_id))
    except Exception as exc:
        logger.error("analyze_task_failed", cv_id=cv_id, job_id=job_id, exc_info=True)
        return {"error": "Analysis failed. Please try again."}


async def _analyze_cv_for_job(user_id: str, cv_id: str, job_id: str) -> dict:
    import uuid as _uuid
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select

    from app.models.user_cv import UserCV
    from app.models.job import Job
    from app.models.cv_analysis import CVAnalysis
    from app.core import ai

    async with async_session_maker() as db:
        # Fetch CV (must be ready and have full_text)
        cv = (await db.execute(
            select(UserCV).where(
                UserCV.id == _uuid.UUID(cv_id),
                UserCV.user_id == _uuid.UUID(user_id),
                UserCV.is_active == True,
            )
        )).scalar_one_or_none()
        if not cv or not cv.full_text:
            return {"error": "CV not found or not processed"}

        # Fetch Job
        job = (await db.execute(
            select(Job).where(Job.id == _uuid.UUID(job_id), Job.is_active == True)
        )).scalar_one_or_none()
        if not job or not job.description:
            return {"error": "Job not found or has no description"}

        # Check cache (24h TTL)
        now = datetime.now(timezone.utc)
        cached = (await db.execute(
            select(CVAnalysis).where(
                CVAnalysis.cv_id == cv.id,
                CVAnalysis.job_id == job.id,
                CVAnalysis.expires_at > now,
            )
        )).scalar_one_or_none()

        if cached:
            return {
                "cv_id": cv_id,
                "job_id": job_id,
                "match_score": cached.match_score,
                "present_keywords": cached.present_keywords,
                "missing_keywords": cached.missing_keywords,
                "suggested_additions": cached.suggested_additions,
                "cached": True,
                "analyzed_at": cached.analyzed_at.isoformat(),
            }

        # Extract JD keywords, then analyze
        jd_keywords = await ai.extract_keywords_from_jd(job.description)
        result = await ai.analyze_cv_against_jd(cv.full_text, job.description, jd_keywords)

        # Store in cache
        analysis = CVAnalysis(
            cv_id=cv.id,
            job_id=job.id,
            user_id=cv.user_id,
            match_score=result.get("match_score", 0.0),
            present_keywords=result.get("present_keywords", []),
            missing_keywords=result.get("missing_keywords", []),
            suggested_additions=result.get("suggested_additions", [])[:5],
            jd_keywords_snapshot=jd_keywords,
            analyzed_at=now,
            expires_at=now + timedelta(hours=24),
        )
        db.add(analysis)
        await db.commit()

        logger.info(
            "cv_analysis_completed",
            cv_id=cv_id,
            job_id=job_id,
            match_score=analysis.match_score,
        )

        return {
            "cv_id": cv_id,
            "job_id": job_id,
            "match_score": analysis.match_score,
            "present_keywords": analysis.present_keywords,
            "missing_keywords": analysis.missing_keywords,
            "suggested_additions": analysis.suggested_additions,
            "cached": False,
            "analyzed_at": analysis.analyzed_at.isoformat(),
        }


# ── CV Tailoring Task ─────────────────────────────────────────────────────────


@celery_app.task(bind=True, max_retries=1, queue="cv_processing")
def tailor_cv(self, user_id: str, cv_id: str, job_id: str):
    """Tailor CV summary + skills for a specific job. Always runs fresh."""
    try:
        return run_async(_tailor_cv(user_id, cv_id, job_id))
    except Exception as exc:
        logger.error("tailor_task_failed", cv_id=cv_id, job_id=job_id, exc_info=True)
        return {"error": "Tailoring failed. Please try again."}


async def _tailor_cv(user_id: str, cv_id: str, job_id: str) -> dict:
    import uuid as _uuid
    from datetime import datetime, timezone
    from sqlalchemy import select

    from app.models.user_cv import UserCV
    from app.models.job import Job
    from app.models.cv_analysis import CVAnalysis
    from app.core import ai

    async with async_session_maker() as db:
        cv = (await db.execute(
            select(UserCV).where(
                UserCV.id == _uuid.UUID(cv_id),
                UserCV.user_id == _uuid.UUID(user_id),
                UserCV.is_active == True,
            )
        )).scalar_one_or_none()
        if not cv or not cv.full_text:
            return {"error": "CV not found or not processed"}

        job = (await db.execute(
            select(Job).where(Job.id == _uuid.UUID(job_id), Job.is_active == True)
        )).scalar_one_or_none()
        if not job or not job.description:
            return {"error": "Job not found or has no description"}

        # Use cached analysis missing_keywords if available
        now = datetime.now(timezone.utc)
        cached_analysis = (await db.execute(
            select(CVAnalysis).where(
                CVAnalysis.cv_id == cv.id,
                CVAnalysis.job_id == job.id,
                CVAnalysis.expires_at > now,
            )
        )).scalar_one_or_none()

        if cached_analysis:
            missing_keywords = cached_analysis.missing_keywords
        else:
            # No cached analysis — extract fresh keywords
            jd_keywords = await ai.extract_keywords_from_jd(job.description)
            analysis_result = await ai.analyze_cv_against_jd(
                cv.full_text, job.description, jd_keywords
            )
            missing_keywords = analysis_result.get("missing_keywords", [])

        # Tailor CV sections
        result = await ai.tailor_cv_section(
            cv.full_text, job.description, missing_keywords
        )

        logger.info(
            "cv_tailor_completed",
            cv_id=cv_id,
            job_id=job_id,
            keywords_added=len(result.get("keywords_added", [])),
        )

        return {
            "cv_id": cv_id,
            "job_id": job_id,
            "tailored_summary": result.get("tailored_summary", ""),
            "tailored_skills": result.get("tailored_skills", []),
            "keywords_added": result.get("keywords_added", []),
            "original_summary": result.get("original_summary", ""),
        }


# ── Text Chunking Helpers ─────────────────────────────────────────────────────


def _chunk_text(
    text: str,
    max_chars: int = 2000,
    overlap_chars: int = 200,
) -> list[dict]:
    """
    Split CV text into overlapping chunks for embedding.

    Paragraph-aware splitting:
    1. Split on double newlines (paragraphs)
    2. Accumulate paragraphs until max_chars reached
    3. Overlap by including the last overlap_chars from the previous chunk

    Returns list of {"text": str, "section": str}
    """
    paragraphs = text.split("\n\n")
    chunks = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 2 > max_chars and current:
            section = _detect_section(current)
            chunks.append({"text": current.strip(), "section": section})
            # Overlap: keep tail of current chunk
            current = current[-overlap_chars:] + "\n\n" + para
        else:
            current = (current + "\n\n" + para).strip()

    if current.strip():
        chunks.append({"text": current.strip(), "section": _detect_section(current)})

    return chunks if chunks else [{"text": text[:max_chars], "section": "other"}]


def _detect_section(text: str) -> str:
    """Heuristic section detection from the first 200 chars of chunk text."""
    lower = text[:200].lower()
    if any(kw in lower for kw in ("summary", "profile", "objective", "about me")):
        return "summary"
    if any(kw in lower for kw in ("experience", "employment", "work history")):
        return "experience"
    if any(kw in lower for kw in ("skill", "technologies", "competenc", "proficienc")):
        return "skills"
    if any(kw in lower for kw in ("education", "degree", "university", "college")):
        return "education"
    if any(kw in lower for kw in ("certif", "license", "award", "honour", "honor")):
        return "certification"
    return "other"


# ── Skills Extraction ─────────────────────────────────────────────────────────


def _extract_skills_from_text(text_lower: str) -> list[tuple[str, str]]:
    """
    Simple keyword-in-text skill extraction against SKILLS_TAXONOMY.

    Returns a list of (skill_name, category) tuples for every taxonomy entry
    found in the CV text.
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
