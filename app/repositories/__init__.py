"""
Repository layer - data access abstraction.

Repositories handle all database queries, keeping SQL/ORM logic
out of the service and route layers.
"""
from app.repositories.base import BaseRepository
from app.repositories.user_repository import UserRepository
from app.repositories.job_repository import JobRepository
from app.repositories.alert_repository import AlertRepository
from app.repositories.company_repository import CompanyRepository
from app.repositories.source_repository import SourceRepository

__all__ = [
    "BaseRepository",
    "UserRepository",
    "JobRepository",
    "AlertRepository",
    "CompanyRepository",
    "SourceRepository",
]
