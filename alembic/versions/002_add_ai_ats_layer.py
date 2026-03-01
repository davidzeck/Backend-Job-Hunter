"""add cv_chunks, cv_analyses tables and user_cvs.full_text

Revision ID: 002_ai_ats_layer
Revises: 001_cv_upload_s3
Create Date: 2026-03-01

Phase 2 — AI/ATS layer:
  • cv_chunks: stores text chunks + embedding vectors from processed CVs
  • cv_analyses: caches CV-vs-JD analysis results (24h TTL)
  • user_cvs.full_text: cached extracted PDF text (avoids re-downloading from S3)
  • user_skills index on cv_id: faster cascade delete lookups
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


# revision identifiers
revision = "002_ai_ats_layer"
down_revision = "001_cv_upload_s3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── cv_chunks table ───────────────────────────────────────────────────
    op.create_table(
        "cv_chunks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("cv_id", UUID(as_uuid=True), sa.ForeignKey("user_cvs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("embedding", JSONB(), nullable=True),
        sa.Column("section_label", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_cv_chunks_cv_id", "cv_chunks", ["cv_id"])
    op.create_index("ix_cv_chunks_user_id", "cv_chunks", ["user_id"])
    op.create_index("ix_cv_chunks_cv_chunk", "cv_chunks", ["cv_id", "chunk_index"], unique=True)

    # ── cv_analyses table ─────────────────────────────────────────────────
    op.create_table(
        "cv_analyses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("cv_id", UUID(as_uuid=True), sa.ForeignKey("user_cvs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("match_score", sa.Float(), nullable=False),
        sa.Column("present_keywords", JSONB(), nullable=False, server_default="[]"),
        sa.Column("missing_keywords", JSONB(), nullable=False, server_default="[]"),
        sa.Column("suggested_additions", JSONB(), nullable=False, server_default="[]"),
        sa.Column("jd_keywords_snapshot", JSONB(), nullable=True),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_cv_analyses_user_id", "cv_analyses", ["user_id"])
    op.create_index("ix_cv_analyses_cv_job", "cv_analyses", ["cv_id", "job_id"])
    op.create_index("ix_cv_analyses_expires", "cv_analyses", ["expires_at"])

    # ── user_cvs.full_text column ─────────────────────────────────────────
    op.add_column(
        "user_cvs",
        sa.Column("full_text", sa.Text(), nullable=True),
    )

    # ── user_skills index on cv_id (faster cascade delete) ────────────────
    op.create_index("ix_user_skills_cv_id", "user_skills", ["cv_id"])


def downgrade() -> None:
    op.drop_index("ix_user_skills_cv_id", table_name="user_skills")
    op.drop_column("user_cvs", "full_text")
    op.drop_table("cv_analyses")
    op.drop_table("cv_chunks")
