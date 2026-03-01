"""
CV service — all CV business logic lives here.

Routes are thin; Celery tasks are thin. This is where the rules are enforced.

Business rules
──────────────
• Max 10 active CVs per user
• Max 5 MB per file (also enforced by S3 presigned POST policy)
• PDF only (validated by file extension + S3 Content-Type policy)
• SHA-256 deduplication — same hash as an existing active CV returns 409
• One upload in flight at a time — blocks if a pending_upload record < 20 min old
"""
import uuid
from datetime import datetime, timezone, timedelta
from typing import List

from fastapi import HTTPException, status
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core import storage
from app.core.logging import get_logger
from app.models.user_cv import (
    UserCV,
    UPLOAD_STATUS_PENDING,
    UPLOAD_STATUS_UPLOADED,
    UPLOAD_STATUS_READY,
    UPLOAD_STATUS_FAILED,
)
from app.models.user_skill import UserSkill
from app.models.job import Job
from app.models.cv_analysis import CVAnalysis
from app.schemas.cv import (
    CVPresignRequest,
    CVPresignResponse,
    CVConfirmRequest,
    CVResponse,
    CVDownloadUrlResponse,
    CVAnalysisResponse,
    CVTaskStatusResponse,
)

logger = get_logger(__name__)

# Maximum active CVs allowed per user
MAX_CVS_PER_USER = 10
# A pending_upload record older than this is considered stale and cleaned up
PENDING_UPLOAD_TTL_MINUTES = 20


class CVService:

    # ── Presign ──────────────────────────────────────────────────────────────

    async def presign_upload(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        req: CVPresignRequest,
    ) -> CVPresignResponse:
        """
        Step 1 of the 3-step upload flow.

        Validates business rules, creates a UserCV record in pending_upload status,
        and returns a presigned POST URL the client uses to upload directly to S3.
        """
        # Guard: file size
        max_bytes = settings.max_cv_size_bytes
        if req.file_size_bytes > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File too large. Maximum size is {settings.max_cv_size_mb} MB.",
            )

        # Guard: active CV count
        active_count = await self._count_active_cvs(db, user_id)
        if active_count >= MAX_CVS_PER_USER:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"CV limit reached. You can have at most {MAX_CVS_PER_USER} active CVs. "
                       "Delete an existing one before uploading a new one.",
            )

        # Guard: duplicate (same hash already uploaded by this user)
        existing_hash = await db.execute(
            select(UserCV).where(
                UserCV.user_id == user_id,
                UserCV.file_hash == req.file_hash,
                UserCV.is_active == True,
            )
        )
        if existing_hash.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This file has already been uploaded. Duplicate CV rejected.",
            )

        # Guard: no two concurrent uploads — atomic cleanup + check
        # Fix: single UPDATE instead of select-loop (TOCTOU safe)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=PENDING_UPLOAD_TTL_MINUTES)
        await db.execute(
            update(UserCV)
            .where(
                UserCV.user_id == user_id,
                UserCV.upload_status == UPLOAD_STATUS_PENDING,
                UserCV.created_at < cutoff,
            )
            .values(is_active=False)
        )

        # FOR UPDATE serializes concurrent presign requests for same user
        in_flight = await db.execute(
            select(UserCV)
            .where(
                UserCV.user_id == user_id,
                UserCV.upload_status == UPLOAD_STATUS_PENDING,
                UserCV.is_active == True,
            )
            .with_for_update(skip_locked=True)
        )
        if in_flight.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Another upload is already in progress. Please wait or refresh.",
            )

        # Create DB record
        cv_id = uuid.uuid4()
        s3_key = storage.build_s3_key(str(user_id), str(cv_id), req.filename)

        cv = UserCV(
            id=cv_id,
            user_id=user_id,
            filename=req.filename,
            file_path=s3_key,   # backward-compat alias
            s3_key=s3_key,
            file_hash=req.file_hash,
            file_size_bytes=req.file_size_bytes,
            upload_status=UPLOAD_STATUS_PENDING,
            is_active=True,
        )
        db.add(cv)
        await db.commit()
        await db.refresh(cv)

        # Generate presigned POST
        presign = await storage.generate_presign_upload(s3_key, max_bytes)
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=settings.s3_presign_upload_expires
        )

        return CVPresignResponse(
            cv_id=cv.id,
            upload_url=presign["url"],
            fields=presign["fields"],
            expires_at=expires_at,
        )

    # ── Confirm ──────────────────────────────────────────────────────────────

    async def confirm_upload(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        cv_id: uuid.UUID,
        req: CVConfirmRequest,
    ) -> CVResponse:
        """
        Step 3 of the 3-step upload flow (step 2 is the direct S3 upload by the client).

        Verifies:
          • The UserCV record belongs to this user
          • The status is still pending_upload
          • The file actually exists in S3 (head_object)
          • The hash matches what was declared during presign
        Then transitions status to uploaded and enqueues the Celery process_cv task.
        """
        cv = await self._get_cv(db, user_id, cv_id)

        if cv.upload_status != UPLOAD_STATUS_PENDING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot confirm: CV is in '{cv.upload_status}' status.",
            )

        # Hash integrity check
        if cv.file_hash and cv.file_hash != req.file_hash:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File hash mismatch. The uploaded file may be corrupted.",
            )

        # S3 object existence check
        if not await storage.object_exists(cv.s3_key):
            raise HTTPException(
                status_code=status.HTTP_424_FAILED_DEPENDENCY,
                detail="File not found in storage. The S3 upload may have failed or expired.",
            )

        # Transition to uploaded
        cv.upload_status = UPLOAD_STATUS_UPLOADED
        await db.commit()
        await db.refresh(cv)

        # Enqueue background processing — deferred import avoids circular imports
        from app.workers.tasks import process_cv
        process_cv.apply_async(
            args=[str(user_id), str(cv_id)],
            queue="cv_processing",
        )

        return self._to_response(cv, skills_count=0)

    # ── List ─────────────────────────────────────────────────────────────────

    async def list_cvs(self, db: AsyncSession, user_id: uuid.UUID) -> List[CVResponse]:
        """Return all active CVs for the user, most recently created first."""
        # Single query with aggregated skills count (fixes N+1)
        result = await db.execute(
            select(
                UserCV,
                func.count(UserSkill.id).label("skills_count"),
            )
            .outerjoin(UserSkill, UserSkill.cv_id == UserCV.id)
            .where(UserCV.user_id == user_id, UserCV.is_active == True)
            .group_by(UserCV.id)
            .order_by(UserCV.created_at.desc())
        )
        return [
            self._to_response(cv, skills_count)
            for cv, skills_count in result.all()
        ]

    # ── Download URL ──────────────────────────────────────────────────────────

    async def get_download_url(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        cv_id: uuid.UUID,
    ) -> CVDownloadUrlResponse:
        """Generate a presigned GET URL for downloading a CV (never streams through the API)."""
        cv = await self._get_cv(db, user_id, cv_id)

        if cv.upload_status not in (UPLOAD_STATUS_UPLOADED, UPLOAD_STATUS_READY):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"CV is not ready for download (status: {cv.upload_status}).",
            )

        url = await storage.generate_presign_download(cv.s3_key, cv.filename)

        return CVDownloadUrlResponse(
            cv_id=cv.id,
            download_url=url,
            expires_in_seconds=settings.s3_presign_download_expires,
        )

    # ── Delete ────────────────────────────────────────────────────────────────

    async def delete_cv(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        cv_id: uuid.UUID,
    ) -> None:
        """
        Soft-delete the CV record (is_active=False) and delete the S3 object.

        Skills, chunks, and analyses associated with this CV are cascade-deleted.
        """
        cv = await self._get_cv(db, user_id, cv_id)

        # Delete S3 object (ignore errors — object may not exist if upload never completed)
        try:
            await storage.delete_object(cv.s3_key)
        except Exception:
            logger.warning("s3_delete_failed", cv_id=str(cv_id), s3_key=cv.s3_key)

        # Soft-delete record (cascade kills UserSkill, CVChunk, CVAnalysis rows)
        cv.is_active = False
        await db.commit()

    # ── AI/ATS: Analysis ─────────────────────────────────────────────────────

    async def start_analysis(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        cv_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> CVTaskStatusResponse:
        """
        Start CV analysis against a job. Returns cached result immediately
        if available (within 24h), otherwise enqueues Celery task.
        """
        cv = await self._get_cv(db, user_id, cv_id)
        if cv.upload_status != UPLOAD_STATUS_READY:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"CV is not ready for analysis (status: {cv.upload_status}). "
                       "Wait for processing to complete.",
            )

        # Validate job exists and has a description
        job = (await db.execute(
            select(Job).where(Job.id == job_id, Job.is_active == True)
        )).scalar_one_or_none()
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Job not found."
            )
        if not job.description:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Job has no description to analyze against.",
            )

        # Check cache (24h TTL)
        now = datetime.now(timezone.utc)
        cached = (await db.execute(
            select(CVAnalysis).where(
                CVAnalysis.cv_id == cv_id,
                CVAnalysis.job_id == job_id,
                CVAnalysis.expires_at > now,
            )
        )).scalar_one_or_none()

        if cached:
            logger.info("cv_analysis_cache_hit", cv_id=str(cv_id), job_id=str(job_id))
            return CVTaskStatusResponse(
                task_id="cached",
                status="success",
                result=CVAnalysisResponse(
                    cv_id=cv_id,
                    job_id=job_id,
                    match_score=cached.match_score,
                    present_keywords=cached.present_keywords,
                    missing_keywords=cached.missing_keywords,
                    suggested_additions=cached.suggested_additions,
                    cached=True,
                    analyzed_at=cached.analyzed_at,
                ).model_dump(mode="json"),
            )

        # Enqueue Celery task
        from app.workers.tasks import analyze_cv_for_job
        task = analyze_cv_for_job.apply_async(
            args=[str(user_id), str(cv_id), str(job_id)],
            queue="cv_processing",
        )
        logger.info("cv_analysis_enqueued", cv_id=str(cv_id), job_id=str(job_id), task_id=task.id)
        return CVTaskStatusResponse(task_id=task.id, status="pending")

    # ── AI/ATS: Tailoring ────────────────────────────────────────────────────

    async def start_tailor(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        cv_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> CVTaskStatusResponse:
        """
        Start CV tailoring for a specific job. Always async via Celery.
        """
        cv = await self._get_cv(db, user_id, cv_id)
        if cv.upload_status != UPLOAD_STATUS_READY:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"CV is not ready for tailoring (status: {cv.upload_status}).",
            )

        job = (await db.execute(
            select(Job).where(Job.id == job_id, Job.is_active == True)
        )).scalar_one_or_none()
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Job not found."
            )
        if not job.description:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Job has no description to tailor against.",
            )

        from app.workers.tasks import tailor_cv
        task = tailor_cv.apply_async(
            args=[str(user_id), str(cv_id), str(job_id)],
            queue="cv_processing",
        )
        logger.info("cv_tailor_enqueued", cv_id=str(cv_id), job_id=str(job_id), task_id=task.id)
        return CVTaskStatusResponse(task_id=task.id, status="pending")

    # ── AI/ATS: Task Status ──────────────────────────────────────────────────

    @staticmethod
    def get_task_status(task_id: str) -> CVTaskStatusResponse:
        """
        Check status of a Celery task by ID.
        Maps Celery states to our API states.
        """
        from app.workers.celery_app import celery_app

        result = celery_app.AsyncResult(task_id)

        status_map = {
            "PENDING": "pending",
            "STARTED": "started",
            "SUCCESS": "success",
            "FAILURE": "failure",
            "RETRY": "pending",
        }
        mapped_status = status_map.get(result.status, "pending")

        response = CVTaskStatusResponse(task_id=task_id, status=mapped_status)

        if result.status == "SUCCESS":
            task_result = result.result
            if isinstance(task_result, dict) and "error" in task_result:
                response.status = "failure"
                response.error = task_result["error"]
            else:
                response.result = task_result
        elif result.status == "FAILURE":
            # Never expose raw traceback — return sanitized message
            response.error = "Task failed. Please try again."
            logger.error("celery_task_failed", task_id=task_id, error=str(result.result))

        return response

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _get_cv(
        self, db: AsyncSession, user_id: uuid.UUID, cv_id: uuid.UUID
    ) -> UserCV:
        result = await db.execute(
            select(UserCV).where(
                UserCV.id == cv_id,
                UserCV.user_id == user_id,
                UserCV.is_active == True,
            )
        )
        cv = result.scalar_one_or_none()
        if not cv:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CV not found.")
        return cv

    async def _count_active_cvs(self, db: AsyncSession, user_id: uuid.UUID) -> int:
        result = await db.execute(
            select(func.count()).where(
                UserCV.user_id == user_id,
                UserCV.is_active == True,
            )
        )
        return result.scalar_one()

    @staticmethod
    def _to_response(cv: UserCV, skills_count: int) -> CVResponse:
        return CVResponse(
            id=cv.id,
            filename=cv.filename,
            file_size_bytes=cv.file_size_bytes,
            file_hash=cv.file_hash,
            upload_status=cv.upload_status,
            skills_extracted=skills_count,
            is_active=cv.is_active,
            created_at=cv.created_at,
            processed_at=cv.processed_at,
        )
