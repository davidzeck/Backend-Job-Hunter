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

    The engine dispose is load-bearing: the module-level engine pools
    connections bound to THIS loop. The next task runs on a new loop, and
    reusing a pooled connection across loops raises asyncpg
    "cannot perform operation: another operation is in progress".
    """
    from app.core.database import engine

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.run_until_complete(engine.dispose())
        except Exception:
            pass  # dispose is best-effort; never mask the task result
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
    from app.core.config import settings
    from app.services.scrape_service import ScrapeService

    scrape_service = ScrapeService()

    async with async_session_maker() as db:
        result = await scrape_service.scrape_source(db, source_id)

        # Validation gate: new jobs are validated before alerting so dead/wrong
        # links never notify users. validate_job fans out to notify on pass.
        # With validation disabled, fall back to notifying directly.
        for job_id in result.get("new_job_ids", []):
            if settings.validation_enabled:
                validate_job.delay(job_id)
            else:
                notify_matching_users.delay(job_id)

        return result


@celery_app.task(bind=True)
def backfill_job_skills(self, batch_size: int = 500):
    """One-off: populate job_skills for existing active jobs that have none.

    Job-skill extraction runs at ingest going forward; this seeds the
    recommendation feed for jobs scraped before that existed. Safe to re-run."""
    return run_async(_backfill_job_skills(batch_size))


async def _backfill_job_skills(batch_size: int):
    from sqlalchemy import select

    from app.models.job import Job
    from app.models.job_skill import JobSkill
    from app.services.scrape_service import ScrapeService

    service = ScrapeService()
    processed = 0
    skills_added = 0

    async with async_session_maker() as db:
        # Active jobs with no job_skills rows yet.
        result = await db.execute(
            select(Job)
            .where(
                Job.is_active == True,  # noqa: E712
                ~Job.id.in_(select(JobSkill.job_id)),
            )
            .limit(batch_size)
        )
        jobs = list(result.scalars().all())
        for job in jobs:
            skills_added += await service._extract_job_skills(db, job)
            processed += 1
        await db.commit()

    return {"processed": processed, "skills_added": skills_added}


@celery_app.task(bind=True, max_retries=2)
def validate_job(self, job_id: str):
    """
    Validate a newly-scraped job's apply URL, then alert on pass.

    Sits between ingest and the alert critical path. Definitive dead/suspect
    results suppress the alert; valid/unverified (incl. network flakiness) let
    it through — a validation hiccup must never silently drop a real job.
    """
    return run_async(_validate_job(job_id))


async def _validate_job(job_id: str):
    from uuid import UUID

    from app.services.validation_service import ValidationService

    async with async_session_maker() as db:
        should_alert = await ValidationService().validate_job(db, UUID(job_id))

    if should_alert:
        notify_matching_users.delay(job_id)
    return {"job_id": job_id, "alerted": should_alert}


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
            if settings.gemini_api_key:
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
            from app.core.skills import extract_skills_from_lower

            text_lower = text.lower()
            extracted = extract_skills_from_lower(text_lower)

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


# Shown when the AI provider itself is out of quota (distinct from our per-user
# daily cap, which is enforced at the API layer before a task is ever enqueued).
_AI_QUOTA_MESSAGE = (
    "AI quota exhausted for today. Top up your AI plan, "
    "or try again after the daily reset."
)


@celery_app.task(bind=True, max_retries=1, queue="cv_processing")
def analyze_cv_for_job(self, user_id: str, cv_id: str, job_id: str):
    """Analyze a CV against a job description. Returns analysis result dict."""
    from app.core.ai import AIQuotaExceededError

    try:
        return run_async(_analyze_cv_for_job(user_id, cv_id, job_id))
    except AIQuotaExceededError:
        logger.warning("analyze_task_quota", cv_id=cv_id, job_id=job_id)
        return {"error": _AI_QUOTA_MESSAGE}
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
    from app.core.ai import AIQuotaExceededError

    try:
        return run_async(_tailor_cv(user_id, cv_id, job_id))
    except AIQuotaExceededError:
        logger.warning("tailor_task_quota", cv_id=cv_id, job_id=job_id)
        return {"error": _AI_QUOTA_MESSAGE}
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


# ── CV Curation & Document Export ─────────────────────────────────────────────


@celery_app.task(bind=True, max_retries=1, queue="cv_processing")
def curate_cv(self, user_id: str, cv_id: str, job_id: str, draft_id: str):
    """
    Stage-1+2 curation: parse the CV into structure (cached per CV), tailor the
    FULL structure against the JD, land the result as a draft in `review`.
    Documents are NOT generated here — the user approves first.
    """
    return run_async(_curate_cv(user_id, cv_id, job_id, draft_id))


async def _curate_cv(user_id: str, cv_id: str, job_id: str, draft_id: str):
    import uuid as _uuid
    from datetime import datetime, timezone
    from sqlalchemy import select

    from app.core import ai
    from app.models.cv_analysis import CVAnalysis
    from app.models.cv_draft import (
        CVDraft,
        DRAFT_STATUS_FAILED,
        DRAFT_STATUS_GENERATING,
        DRAFT_STATUS_REVIEW,
    )
    from app.models.job import Job
    from app.models.user_cv import UserCV
    from app.schemas.cv import CVStructure

    async with async_session_maker() as db:
        draft = (await db.execute(
            select(CVDraft).where(CVDraft.id == _uuid.UUID(draft_id))
        )).scalar_one_or_none()
        if not draft or draft.status != DRAFT_STATUS_GENERATING:
            return {"error": "Draft not found or not in generating state"}

        async def fail(public_message: str):
            draft.status = DRAFT_STATUS_FAILED
            draft.error = public_message  # sanitized — full detail is in logs
            await db.commit()
            return {"error": public_message, "draft_id": draft_id}

        cv = (await db.execute(
            select(UserCV).where(
                UserCV.id == _uuid.UUID(cv_id),
                UserCV.user_id == _uuid.UUID(user_id),
                UserCV.is_active == True,
            )
        )).scalar_one_or_none()
        if not cv or not cv.full_text:
            return await fail("CV not found or not processed")

        job = (await db.execute(
            select(Job).where(Job.id == _uuid.UUID(job_id), Job.is_active == True)
        )).scalar_one_or_none()
        if not job or not job.description:
            return await fail("Job not found or has no description")

        try:
            # Stage 1 — structure parse, cached once per CV.
            if cv.parsed_structure:
                structure = cv.parsed_structure
            else:
                raw_structure = await ai.parse_cv_structure(cv.full_text)
                structure = CVStructure.model_validate(raw_structure).model_dump()
                cv.parsed_structure = structure
                await db.flush()

            # Missing keywords: reuse a fresh cached analysis (same as _tailor_cv).
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
                jd_keywords = await ai.extract_keywords_from_jd(job.description)
                analysis_result = await ai.analyze_cv_against_jd(
                    cv.full_text, job.description, jd_keywords
                )
                missing_keywords = analysis_result.get("missing_keywords", [])

            # Stage 2 — full-structure tailoring.
            result = await ai.tailor_cv_full(
                structure, job.description, missing_keywords
            )
            tailored = CVStructure.model_validate(result.get("tailored") or {})
            if not tailored.summary and not tailored.experience:
                # Model returned an effectively-empty CV — don't put that in review.
                return await fail("Curation produced no usable content. Try again.")

        except ai.AIQuotaExceededError:
            return await fail("AI quota exhausted. Try again later.")
        except Exception as exc:
            logger.error(
                "cv_curate_failed", draft_id=draft_id, cv_id=cv_id,
                job_id=job_id, error=str(exc),
            )
            return await fail("Curation failed. Please try again.")

        draft.content = {
            "original": structure,
            "tailored": tailored.model_dump(),
            "keywords_injected": [
                str(k) for k in result.get("keywords_injected", [])
            ],
        }
        draft.status = DRAFT_STATUS_REVIEW
        await db.commit()

        logger.info(
            "cv_curate_completed", draft_id=draft_id, cv_id=cv_id, job_id=job_id,
            keywords_injected=len(draft.content["keywords_injected"]),
        )
        return {"draft_id": draft_id, "status": DRAFT_STATUS_REVIEW}


@celery_app.task(bind=True, max_retries=2, queue="cv_processing")
def generate_cv_document(self, draft_id: str):
    """Render an APPROVED draft's tailored structure to DOCX + PDF in S3."""
    return run_async(_generate_cv_document(draft_id))


async def _generate_cv_document(draft_id: str):
    import uuid as _uuid
    from sqlalchemy import select

    from app.core import storage
    from app.core.docgen import render_docx, render_pdf
    from app.models.cv_draft import (
        CVDraft,
        DRAFT_STATUS_APPROVED,
        DRAFT_STATUS_FAILED,
        DRAFT_STATUS_RENDERED,
    )

    async with async_session_maker() as db:
        draft = (await db.execute(
            select(CVDraft).where(CVDraft.id == _uuid.UUID(draft_id))
        )).scalar_one_or_none()
        if not draft or draft.status != DRAFT_STATUS_APPROVED:
            return {"error": "Draft not found or not approved"}

        tailored = (draft.content or {}).get("tailored")
        if not tailored:
            draft.status = DRAFT_STATUS_FAILED
            draft.error = "Draft has no content to render"
            await db.commit()
            return {"error": draft.error}

        base_key = storage.build_s3_key(
            str(draft.user_id), str(draft.cv_id), "x"
        ).rsplit("/", 1)[0] + f"/generated/{draft_id}"

        try:
            docx_bytes = render_docx(tailored)
            pdf_bytes = render_pdf(tailored)
            docx_key = f"{base_key}/cv.docx"
            pdf_key = f"{base_key}/cv.pdf"
            await storage.upload_bytes(
                docx_key, docx_bytes,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            await storage.upload_bytes(pdf_key, pdf_bytes, "application/pdf")
        except Exception as exc:
            logger.error("cv_render_failed", draft_id=draft_id, error=str(exc))
            draft.status = DRAFT_STATUS_FAILED
            draft.error = "Document generation failed. Please try again."
            await db.commit()
            return {"error": draft.error}

        draft.docx_s3_key = docx_key
        draft.pdf_s3_key = pdf_key
        draft.status = DRAFT_STATUS_RENDERED
        await db.commit()

        logger.info(
            "cv_render_completed", draft_id=draft_id,
            docx_bytes=len(docx_bytes), pdf_bytes=len(pdf_bytes),
        )
        return {"draft_id": draft_id, "status": DRAFT_STATUS_RENDERED}


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


# Skill taxonomy + extraction moved to app/core/skills.py (shared with job
# ingestion so user_skills and job_skills use the same vocabulary).
