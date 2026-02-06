"""
Database models for Job Scout.

All models use UUID primary keys and include created_at/updated_at timestamps.
"""
from app.models.base import BaseModel, TimestampMixin, UUIDMixin
from app.models.company import Company
from app.models.job_source import JobSource
from app.models.job import Job
from app.models.user import User
from app.models.user_cv import UserCV
from app.models.user_skill import UserSkill
from app.models.job_skill import JobSkill
from app.models.user_job_alert import UserJobAlert
from app.models.scrape_log import ScrapeLog

__all__ = [
    "BaseModel",
    "TimestampMixin",
    "UUIDMixin",
    "Company",
    "JobSource",
    "Job",
    "User",
    "UserCV",
    "UserSkill",
    "JobSkill",
    "UserJobAlert",
    "ScrapeLog",
]
