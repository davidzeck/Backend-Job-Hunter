"""
CVChunk model — stores text chunks + embeddings from processed CVs.

Each CV is split into overlapping chunks (~2000 chars each) after text extraction.
Embeddings are generated via text-embedding-3-small and stored as JSONB arrays.
When OPENAI_API_KEY is not set, chunks are stored without embeddings.
"""
import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import String, Text, Integer, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.user_cv import UserCV


class CVChunk(BaseModel):
    __tablename__ = "cv_chunks"

    # Foreign Keys
    cv_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user_cvs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    # Chunk data
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)

    # Embedding vector stored as JSON array of floats
    # 1536-d for text-embedding-3-small. Nullable — None when no API key.
    embedding: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    # Heuristic section label: summary, experience, skills, education, certification, other
    section_label: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Relationships
    cv: Mapped["UserCV"] = relationship("UserCV", back_populates="chunks")

    __table_args__ = (
        Index("ix_cv_chunks_cv_chunk", "cv_id", "chunk_index", unique=True),
    )

    def __repr__(self) -> str:
        return f"<CVChunk cv_id={self.cv_id} index={self.chunk_index}>"
