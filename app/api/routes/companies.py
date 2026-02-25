"""
Company routes.

Thin controllers - CompanyService handles the N+1 query optimization
and response construction.
"""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.company import Company
from app.models.job import Job
from app.models.job_source import JobSource
from app.services.company_service import CompanyService
from app.schemas.company import CompanyResponse, CompanyCreate, CompanyUpdate
from app.schemas.base import MessageResponse

router = APIRouter(prefix="/companies", tags=["companies"])

company_service = CompanyService()


async def _build_company_response(company: Company, db: AsyncSession) -> CompanyResponse:
    """Build CompanyResponse with live job/source counts."""
    job_count_res = await db.execute(
        select(func.count()).where(
            Job.company_id == company.id,
            Job.is_active == True,
        )
    )
    source_count_res = await db.execute(
        select(func.count()).where(
            JobSource.company_id == company.id,
            JobSource.is_active == True,
        )
    )
    return CompanyResponse(
        id=company.id,
        name=company.name,
        slug=company.slug,
        careers_url=company.careers_url,
        logo_url=company.logo_url,
        description=company.description,
        is_active=company.is_active,
        created_at=company.created_at,
        updated_at=company.updated_at,
        jobs_count=job_count_res.scalar() or 0,
        sources_count=source_count_res.scalar() or 0,
    )


@router.get("/", response_model=List[CompanyResponse])
async def list_companies(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all monitored companies with job/source counts."""
    return await company_service.list_companies(db)


@router.post("/", response_model=CompanyResponse, status_code=201)
async def create_company(
    data: CompanyCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new company."""
    # Check slug uniqueness
    existing = await db.execute(
        select(Company).where(Company.slug == data.slug)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="A company with this slug already exists")

    company = Company(
        name=data.name,
        slug=data.slug,
        careers_url=data.careers_url,
        logo_url=data.logo_url,
        description=data.description,
        is_active=True,
    )
    db.add(company)
    await db.flush()
    await db.refresh(company)
    await db.commit()
    return await _build_company_response(company, db)


@router.get("/{company_id}", response_model=CompanyResponse)
async def get_company(
    company_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single company by ID."""
    result = await db.execute(
        select(Company).where(Company.id == company_id)
    )
    company = result.scalar_one_or_none()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return await _build_company_response(company, db)


@router.patch("/{company_id}", response_model=CompanyResponse)
async def update_company(
    company_id: UUID,
    data: CompanyUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a company's details."""
    result = await db.execute(
        select(Company).where(Company.id == company_id)
    )
    company = result.scalar_one_or_none()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    if data.name is not None:
        company.name = data.name
    if data.careers_url is not None:
        company.careers_url = data.careers_url
    if data.logo_url is not None:
        company.logo_url = data.logo_url
    if data.description is not None:
        company.description = data.description
    if data.is_active is not None:
        company.is_active = data.is_active

    await db.flush()
    await db.refresh(company)
    await db.commit()
    return await _build_company_response(company, db)


@router.delete("/{company_id}", response_model=MessageResponse)
async def delete_company(
    company_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a company (sets is_active=False)."""
    result = await db.execute(
        select(Company).where(Company.id == company_id)
    )
    company = result.scalar_one_or_none()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    company.is_active = False
    await db.flush()
    await db.commit()
    return MessageResponse(message="Company deleted successfully")
