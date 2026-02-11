"""
Company routes.

Thin controllers - CompanyService handles the N+1 query optimization
and response construction.
"""
from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.services.company_service import CompanyService
from app.schemas.company import CompanyResponse

router = APIRouter(prefix="/companies", tags=["companies"])

company_service = CompanyService()


@router.get("/", response_model=List[CompanyResponse])
async def list_companies(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all monitored companies with job/source counts."""
    return await company_service.list_companies(db)
