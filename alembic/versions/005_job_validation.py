"""add job validation columns

Revision ID: 005_job_validation
Revises: 004_user_job_interactions
Create Date: 2026-07-11

Adds validation tracking to jobs (roadmap Phase A, feature #3):
  • validation_status (VARCHAR 20, not null, default 'unverified', indexed)
    — unverified | valid | suspect | dead
  • last_validated_at (timestamptz, nullable)
  • validation_detail (JSONB, nullable) — per-check outcomes / http status / host
  • duplicate_of_job_id (UUID FK jobs.id, nullable) — cross-source duplicate link
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers
revision = "005_job_validation"
down_revision = "004_user_job_interactions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "validation_status",
            sa.String(length=20),
            nullable=False,
            server_default="unverified",  # existing rows are unverified, not suppressed
        ),
    )
    op.create_index("ix_jobs_validation_status", "jobs", ["validation_status"])

    op.add_column(
        "jobs",
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("validation_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("duplicate_of_job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_jobs_duplicate_of_job_id",
        "jobs",
        "jobs",
        ["duplicate_of_job_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_jobs_duplicate_of_job_id", "jobs", type_="foreignkey")
    op.drop_column("jobs", "duplicate_of_job_id")
    op.drop_column("jobs", "validation_detail")
    op.drop_column("jobs", "last_validated_at")
    op.drop_index("ix_jobs_validation_status", table_name="jobs")
    op.drop_column("jobs", "validation_status")
