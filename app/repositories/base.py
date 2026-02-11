"""
Base repository with generic CRUD operations.

All entity-specific repositories inherit from this.
"""
from typing import Any, Generic, List, Optional, Type, TypeVar
from uuid import UUID

from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import BaseModel

# Generic type for SQLAlchemy models
ModelType = TypeVar("ModelType", bound=BaseModel)


class BaseRepository(Generic[ModelType]):
    """
    Base repository providing standard CRUD operations.

    Usage:
        class UserRepository(BaseRepository[User]):
            def __init__(self):
                super().__init__(User)
    """

    def __init__(self, model: Type[ModelType]):
        self.model = model

    async def get_by_id(
        self,
        db: AsyncSession,
        id: UUID,
    ) -> Optional[ModelType]:
        """Get a single record by ID."""
        result = await db.execute(
            select(self.model).where(self.model.id == id)
        )
        return result.scalar_one_or_none()

    async def get_many(
        self,
        db: AsyncSession,
        *,
        skip: int = 0,
        limit: int = 100,
        order_by: Any = None,
    ) -> List[ModelType]:
        """Get multiple records with pagination."""
        query = select(self.model)

        if order_by is not None:
            query = query.order_by(order_by)
        else:
            query = query.order_by(self.model.created_at.desc())

        query = query.offset(skip).limit(limit)
        result = await db.execute(query)
        return list(result.scalars().all())

    async def count(
        self,
        db: AsyncSession,
    ) -> int:
        """Get total count of records."""
        result = await db.execute(
            select(func.count()).select_from(self.model)
        )
        return result.scalar() or 0

    async def create(
        self,
        db: AsyncSession,
        **kwargs: Any,
    ) -> ModelType:
        """Create a new record."""
        instance = self.model(**kwargs)
        db.add(instance)
        await db.flush()
        await db.refresh(instance)
        return instance

    async def update(
        self,
        db: AsyncSession,
        instance: ModelType,
        **kwargs: Any,
    ) -> ModelType:
        """Update an existing record."""
        for key, value in kwargs.items():
            if hasattr(instance, key):
                setattr(instance, key, value)
        await db.flush()
        await db.refresh(instance)
        return instance

    async def delete(
        self,
        db: AsyncSession,
        id: UUID,
    ) -> bool:
        """Hard delete a record by ID."""
        result = await db.execute(
            delete(self.model).where(self.model.id == id)
        )
        return result.rowcount > 0

    async def soft_delete(
        self,
        db: AsyncSession,
        instance: ModelType,
    ) -> ModelType:
        """Soft delete by setting is_active = False."""
        instance.is_active = False
        await db.flush()
        return instance
