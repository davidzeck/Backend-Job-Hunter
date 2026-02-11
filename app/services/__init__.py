"""
Service layer - business logic and orchestration.

Services contain the application's business logic, coordinate between
repositories, and handle cross-cutting concerns.

RULE: Routes call services. Services call repositories. Never the reverse.
"""
from app.services.auth_service import AuthService
from app.services.job_service import JobService
from app.services.user_service import UserService
from app.services.company_service import CompanyService
from app.services.alert_service import AlertService
from app.services.notification_service import NotificationService
from app.services.scrape_service import ScrapeService

__all__ = [
    "AuthService",
    "JobService",
    "UserService",
    "CompanyService",
    "AlertService",
    "NotificationService",
    "ScrapeService",
]
