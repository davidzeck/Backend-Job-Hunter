"""add user_job_interactions table

Revision ID: 004_user_job_interactions
Revises: 003_auth_sessions
Create Date: 2026-07-08

Browse-view Save/Applied actions, kept separate from user_job_alerts (the
notifications/matches feed) so manual saves don't pollute that feed. One row
per (user_id, job_id).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers
revision = "004_user_job_interactions"
down_revision = "003_auth_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_job_interactions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("saved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "job_id", name="uq_user_job_interaction"),
    )
    op.create_index("ix_user_job_interactions_user_id", "user_job_interactions", ["user_id"])
    op.create_index("ix_user_job_interactions_job_id", "user_job_interactions", ["job_id"])
    op.create_index("ix_user_job_interactions_saved", "user_job_interactions", ["saved"])


def downgrade() -> None:
    op.drop_table("user_job_interactions")
