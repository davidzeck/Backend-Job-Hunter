"""
CV-related Pydantic schemas.

Tier 1 — Storage:
  CVPresignRequest    — client sends before uploading (filename, size, hash)
  CVPresignResponse   — server returns presigned POST URL + S3 fields + cv_id
  CVConfirmRequest    — client sends after S3 upload (file_hash for integrity check)
  CVResponse          — full CV record returned to client
  CVDownloadUrlResponse — short-lived S3 GET URL

Tier 2 — AI/ATS (placeholders defined here, implemented later):
  CVAnalysisResponse  — ATS keyword gap analysis result
  CVTailorResponse    — LLM-rewritten summary + skills
  CVTaskStatusResponse — poll a long-running Celery task
"""
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.base import BaseSchema


# ── Tier 1 ──────────────────────────────────────────────────────────────────

class CVPresignRequest(BaseSchema):
    """Sent by the client to request a presigned S3 upload URL."""

    filename: str = Field(..., min_length=1, max_length=255)
    file_size_bytes: int = Field(..., gt=0)
    file_hash: str = Field(..., min_length=64, max_length=64, description="SHA-256 hex digest")

    @field_validator("filename")
    @classmethod
    def filename_must_be_pdf(cls, v: str) -> str:
        if not v.lower().endswith(".pdf"):
            raise ValueError("Only PDF files are accepted")
        return v


class CVPresignResponse(BaseSchema):
    """
    Returned to client after the presign step.

    The client must:
      1. POST the file directly to `upload_url` including all `fields` FIRST,
         then the `file` field last (S3 presigned POST constraint).
      2. Call confirm with cv_id once the S3 upload returns 204.
    """

    cv_id: UUID
    upload_url: str
    fields: Dict[str, Any]  # S3 policy fields that must precede the file binary
    expires_at: datetime


class CVConfirmRequest(BaseSchema):
    """Sent by the client after the S3 upload succeeds, to trigger Celery processing."""

    file_hash: str = Field(..., min_length=64, max_length=64, description="SHA-256 hex digest")


class CVResponse(BaseSchema):
    """Full CV record as seen by the client."""

    id: UUID
    filename: str
    file_size_bytes: Optional[int]
    file_hash: Optional[str]
    upload_status: str          # pending_upload | uploaded | processing | ready | failed
    skills_extracted: int = 0   # convenience count from len(skills)
    is_active: bool
    created_at: datetime
    processed_at: Optional[datetime]


class CVDownloadUrlResponse(BaseSchema):
    """Short-lived presigned GET URL for downloading a CV."""

    cv_id: UUID
    download_url: str
    expires_in_seconds: int


# ── Tier 2 — AI/ATS ──────────────────────────────────────────────────────────

class CVAnalyzeRequest(BaseSchema):
    """Request body for analyze and tailor endpoints."""

    job_id: UUID


class SkillKeyword(BaseSchema):
    """A single skill/keyword from ATS analysis."""

    name: str
    category: Optional[str] = None


class CVAnalysisResponse(BaseSchema):
    """ATS keyword gap analysis result (cached for 24 h in cv_analyses table)."""

    cv_id: UUID
    job_id: UUID
    match_score: float          # 0.0–1.0 cosine similarity
    present_keywords: List[str] # Keywords in both the JD and the CV
    missing_keywords: List[str] # Keywords in JD but absent from CV
    suggested_additions: List[str]  # Priority-ordered ATS suggestions
    cached: bool
    analyzed_at: datetime


class CVTailorResponse(BaseSchema):
    """
    LLM-rewritten CV sections tailored for a specific job.

    Only the summary and skills list are touched.
    Work history is never modified (no fabrication).
    """

    cv_id: UUID
    job_id: UUID
    tailored_summary: str
    tailored_skills: List[str]
    keywords_added: List[str]   # Which missing keywords were incorporated
    original_summary: str       # For side-by-side comparison in the UI


class CVTaskStatusResponse(BaseSchema):
    """Polling response for long-running CV tasks (analysis / tailor)."""

    task_id: str
    status: str             # pending | started | success | failure
    result: Optional[Any] = None
    error: Optional[str] = None
