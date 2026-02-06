"""
Scrapers package - job scraping infrastructure.
"""
from app.scrapers.base import (
    BaseScraper,
    StaticScraper,
    APIScraper,
    ScrapedJob,
    ScrapeResult,
)
from app.scrapers.registry import get_scraper, list_scrapers, SCRAPER_REGISTRY

__all__ = [
    "BaseScraper",
    "StaticScraper",
    "APIScraper",
    "ScrapedJob",
    "ScrapeResult",
    "get_scraper",
    "list_scrapers",
    "SCRAPER_REGISTRY",
]
