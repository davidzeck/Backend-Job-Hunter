"""add cv_drafts table and user_cvs.parsed_structure

Revision ID: 006_cv_drafts
Revises: 005_job_validation
Create Date: 2026-07-11

CV document export (roadmap Phase A #4):
  • cv_drafts — AI-curated CV rewrites awaiting review/approval; documents are
    only rendered from approved drafts. Status machine:
    generating → review → approved → rendered | failed | superseded
  • user_cvs.parsed_structure (JSONB) — cached stage-1 AI parse of full_text
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers
revision = "006_cv_drafts"
down_revision = "005_job_validation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cv_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "cv_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_cvs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="generating"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("docx_s3_key", sa.Text(), nullable=True),
        sa.Column("pdf_s3_key", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_cv_drafts_user_id", "cv_drafts", ["user_id"])
    op.create_index("ix_cv_drafts_status", "cv_drafts", ["status"])
    op.create_index("ix_cv_drafts_user_status", "cv_drafts", ["user_id", "status"])
    op.create_index("ix_cv_drafts_cv_job", "cv_drafts", ["cv_id", "job_id"])

    op.add_column(
        "user_cvs",
        sa.Column("parsed_structure", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_cvs", "parsed_structure")
    op.drop_index("ix_cv_drafts_cv_job", table_name="cv_drafts")
    op.drop_index("ix_cv_drafts_user_status", table_name="cv_drafts")
    op.drop_index("ix_cv_drafts_status", table_name="cv_drafts")
    op.drop_index("ix_cv_drafts_user_id", table_name="cv_drafts")
    op.drop_table("cv_drafts")
