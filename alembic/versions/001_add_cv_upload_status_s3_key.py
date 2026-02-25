"""add upload_status and s3_key to user_cvs

Revision ID: 001_cv_upload_s3
Revises:
Create Date: 2026-02-25

Adds two new columns to user_cvs:
  • upload_status (VARCHAR 20, not null, default 'pending_upload', indexed)
    — state machine: pending_upload → uploaded → processing → ready / failed
  • s3_key (TEXT, nullable)
    — canonical S3 object key; file_path keeps its old value for backward compat

Also adds:
  • index on file_hash (for SHA-256 deduplication lookups)
  • composite index (user_id, is_active) for "list active CVs" queries
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "001_cv_upload_s3"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add upload_status column with a default so existing rows are valid
    op.add_column(
        "user_cvs",
        sa.Column(
            "upload_status",
            sa.String(length=20),
            nullable=False,
            server_default="ready",  # existing rows treated as already processed
        ),
    )
    op.create_index(
        "ix_user_cvs_upload_status",
        "user_cvs",
        ["upload_status"],
    )

    # Add s3_key column (nullable — existing rows had local disk paths)
    op.add_column(
        "user_cvs",
        sa.Column("s3_key", sa.Text(), nullable=True),
    )

    # Index on file_hash for fast deduplication
    op.create_index(
        "ix_user_cvs_file_hash",
        "user_cvs",
        ["file_hash"],
    )

    # Composite index: user_id + is_active (most common query pattern)
    op.create_index(
        "ix_user_cvs_user_active",
        "user_cvs",
        ["user_id", "is_active"],
    )

    # Index on is_active (used in soft-delete filters)
    op.create_index(
        "ix_user_cvs_is_active",
        "user_cvs",
        ["is_active"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_cvs_is_active", table_name="user_cvs")
    op.drop_index("ix_user_cvs_user_active", table_name="user_cvs")
    op.drop_index("ix_user_cvs_file_hash", table_name="user_cvs")
    op.drop_column("user_cvs", "s3_key")
    op.drop_index("ix_user_cvs_upload_status", table_name="user_cvs")
    op.drop_column("user_cvs", "upload_status")
