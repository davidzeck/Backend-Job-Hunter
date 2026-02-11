"""
Company service - business logic for company listing and management.

Why does this exist when companies are simple CRUD?
Because even "simple" operations have business decisions:
- Should we show inactive companies? (No, for public APIs)
- What counts do we include? (Jobs + sources, not raw DB results)
- How do we format the response? (Consistent with the schema)

Keeping this in a service means the route stays dumb and testable.
"""
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.company_repository import CompanyRepository
from app.schemas.company import CompanyResponse


class CompanyService:
    """Handles company listing and management."""

    def __init__(self):
        self.company_repo = CompanyRepository()

    async def list_companies(
        self,
        db: AsyncSession,
    ) -> List[CompanyResponse]:
        """
        Get all active companies with their job and source counts.

        Why not paginate? Companies are a small, bounded set (5-50 max).
        Unlike jobs which grow unbounded, company list fits in one response.
        """
        enriched = await self.company_repo.get_with_counts(db, active_only=True)

        return [
            CompanyResponse(
                id=item["company"].id,
                name=item["company"].name,
                slug=item["company"].slug,
                careers_url=item["company"].careers_url,
                logo_url=item["company"].logo_url,
                description=item["company"].description,
                is_active=item["company"].is_active,
                created_at=item["company"].created_at,
                updated_at=item["company"].updated_at,
                jobs_count=item["active_jobs"],
                sources_count=item["active_sources"],
            )
            for item in enriched
        ]
