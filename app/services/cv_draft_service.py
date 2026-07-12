"""
CV draft service — full-CV curation drafts and document export.

The human review gate is the core rule here: AI output lands as a DRAFT the
user reviews (and may edit); documents are only rendered from an APPROVED
draft, and downloads only exist once RENDERED.

Status machine (constants in app/models/cv_draft.py):
    generating → review → approved → rendered
         └─────────┴─────────┴──▶ failed
    any non-terminal draft for the same (cv, job) → superseded by a new curate
"""
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.core.logging import get_logger
from app.models.cv_draft import (
    CVDraft,
    DRAFT_STATUS_GENERATING,
    DRAFT_STATUS_REVIEW,
    DRAFT_STATUS_APPROVED,
    DRAFT_STATUS_RENDERED,
    DRAFT_STATUS_SUPERSEDED,
)
from app.models.job import Job
from app.models.user_cv import UserCV, UPLOAD_STATUS_READY
from app.schemas.cv import CVDraftResponse, CVStructure

logger = get_logger(__name__)

_LIVE_STATUSES = (DRAFT_STATUS_GENERATING, DRAFT_STATUS_REVIEW, DRAFT_STATUS_APPROVED)


class CVDraftService:

    # ── Curate ────────────────────────────────────────────────────────────────

    async def start_curate(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        cv_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> dict:
        """
        Start a full-CV curation draft against a job. Supersedes any live draft
        for the same (cv, job) and enqueues the curate_cv task.

        Returns {"task_id", "draft_id", "status"}.
        """
        cv = await self._get_cv(db, user_id, cv_id)
        if cv.upload_status != UPLOAD_STATUS_READY:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"CV is not ready for curation (status: {cv.upload_status}).",
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
                detail="Job has no description to curate against.",
            )

        # A new curate supersedes any live draft for this (cv, job).
        await db.execute(
            update(CVDraft)
            .where(
                CVDraft.cv_id == cv_id,
                CVDraft.job_id == job_id,
                CVDraft.status.in_(_LIVE_STATUSES),
            )
            .values(status=DRAFT_STATUS_SUPERSEDED)
        )

        draft = CVDraft(
            cv_id=cv_id,
            job_id=job_id,
            user_id=user_id,
            status=DRAFT_STATUS_GENERATING,
        )
        db.add(draft)
        await db.commit()

        from app.workers.tasks import curate_cv
        task = curate_cv.apply_async(
            args=[str(user_id), str(cv_id), str(job_id), str(draft.id)],
            queue="cv_processing",
        )
        logger.info(
            "cv_curate_enqueued",
            cv_id=str(cv_id), job_id=str(job_id),
            draft_id=str(draft.id), task_id=task.id,
        )
        return {"task_id": task.id, "draft_id": str(draft.id), "status": "pending"}

    # ── Read ──────────────────────────────────────────────────────────────────

    async def list_drafts(
        self, db: AsyncSession, user_id: uuid.UUID
    ) -> List[CVDraftResponse]:
        """The caller's drafts, newest first (superseded ones excluded)."""
        result = await db.execute(
            select(CVDraft)
            .where(
                CVDraft.user_id == user_id,
                CVDraft.status != DRAFT_STATUS_SUPERSEDED,
            )
            .order_by(CVDraft.created_at.desc())
        )
        return [self._to_response(d) for d in result.scalars().all()]

    async def get_draft(
        self, db: AsyncSession, user_id: uuid.UUID, draft_id: uuid.UUID
    ) -> CVDraftResponse:
        draft = await self._get_owned(db, user_id, draft_id)
        return self._to_response(draft)

    # ── Review edits ──────────────────────────────────────────────────────────

    async def update_draft(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        draft_id: uuid.UUID,
        tailored: CVStructure,
    ) -> CVDraftResponse:
        """Persist user edits to the tailored structure. Review stage only."""
        draft = await self._get_owned(db, user_id, draft_id)
        if draft.status != DRAFT_STATUS_REVIEW:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Draft is not editable (status: {draft.status}).",
            )
        content = dict(draft.content or {})
        content["tailored"] = tailored.model_dump()
        draft.content = content
        await db.commit()
        return self._to_response(draft)

    # ── Approve → render ─────────────────────────────────────────────────────

    async def approve_draft(
        self, db: AsyncSession, user_id: uuid.UUID, draft_id: uuid.UUID
    ) -> dict:
        """
        Approve a reviewed draft and enqueue document generation.

        FOR UPDATE guard: two concurrent approves can't both transition —
        same TOCTOU pattern as the upload-confirm step.
        """
        draft = (await db.execute(
            select(CVDraft)
            .where(CVDraft.id == draft_id, CVDraft.user_id == user_id)
            .with_for_update()
        )).scalar_one_or_none()
        if not draft:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found."
            )
        if draft.status != DRAFT_STATUS_REVIEW:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Draft cannot be approved (status: {draft.status}).",
            )

        draft.status = DRAFT_STATUS_APPROVED
        draft.approved_at = datetime.now(timezone.utc)
        await db.commit()

        from app.workers.tasks import generate_cv_document
        task = generate_cv_document.apply_async(
            args=[str(draft_id)], queue="cv_processing"
        )
        logger.info("cv_draft_approved", draft_id=str(draft_id), task_id=task.id)
        return {"task_id": task.id, "draft_id": str(draft_id), "status": "pending"}

    # ── Download ──────────────────────────────────────────────────────────────

    async def get_download_url(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        draft_id: uuid.UUID,
        format: str,
    ) -> dict:
        """Presigned GET for a rendered document. 409 until rendered."""
        if format not in ("docx", "pdf"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="format must be 'docx' or 'pdf'.",
            )
        draft = await self._get_owned(db, user_id, draft_id)
        if draft.status != DRAFT_STATUS_RENDERED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Documents not ready (status: {draft.status}).",
            )
        s3_key = draft.docx_s3_key if format == "docx" else draft.pdf_s3_key
        if not s3_key:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"No {format} artifact for this draft.",
            )

        from app.core.config import settings
        url = await storage.generate_presign_download(
            s3_key, filename=f"cv-tailored.{format}"
        )
        return {
            "draft_id": str(draft_id),
            "format": format,
            "download_url": url,
            "expires_in": settings.s3_presign_download_expires,
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _get_cv(
        self, db: AsyncSession, user_id: uuid.UUID, cv_id: uuid.UUID
    ) -> UserCV:
        cv = (await db.execute(
            select(UserCV).where(
                UserCV.id == cv_id,
                UserCV.user_id == user_id,
                UserCV.is_active == True,
            )
        )).scalar_one_or_none()
        if not cv:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="CV not found."
            )
        return cv

    async def _get_owned(
        self, db: AsyncSession, user_id: uuid.UUID, draft_id: uuid.UUID
    ) -> CVDraft:
        draft = (await db.execute(
            select(CVDraft).where(
                CVDraft.id == draft_id, CVDraft.user_id == user_id
            )
        )).scalar_one_or_none()
        if not draft:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found."
            )
        return draft

    @staticmethod
    def _to_response(draft: CVDraft) -> CVDraftResponse:
        return CVDraftResponse(
            id=draft.id,
            cv_id=draft.cv_id,
            job_id=draft.job_id,
            status=draft.status,
            content=draft.content,
            error=draft.error,
            docx_ready=bool(draft.docx_s3_key),
            pdf_ready=bool(draft.pdf_s3_key),
            approved_at=draft.approved_at,
            created_at=draft.created_at,
            updated_at=draft.updated_at,
        )
