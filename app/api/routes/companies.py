"""
Company routes.
"""
from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.company import Company
from app.models.job import Job
from app.models.job_source import JobSource
from app.schemas.company import CompanyResponse

router = APIRouter(prefix="/companies", tags=["companies"])


@router.get("/", response_model=List[CompanyResponse])
async def list_companies(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    List all monitored companies.
    """
    # Get companies with counts
    result = await db.execute(
        select(Company).where(Company.is_active == True).order_by(Company.name)
    )
    companies = result.scalars().all()

    # Get counts for each company
    responses = []
    for company in companies:
        # Jobs count
        jobs_result = await db.execute(
            select(func.count(Job.id)).where(
                Job.company_id == company.id,
                Job.is_active == True,
            )
        )
        jobs_count = jobs_result.scalar() or 0

        # Sources count
        sources_result = await db.execute(
            select(func.count(JobSource.id)).where(
                JobSource.company_id == company.id,
                JobSource.is_active == True,
            )
        )
        sources_count = sources_result.scalar() or 0

        responses.append(
            CompanyResponse(
                id=company.id,
                name=company.name,
                slug=company.slug,
                careers_url=company.careers_url,
                logo_url=company.logo_url,
                description=company.description,
                is_active=company.is_active,
                created_at=company.created_at,
                updated_at=company.updated_at,
                jobs_count=jobs_count,
                sources_count=sources_count,
            )
        )

    return responses
