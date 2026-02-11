"""
Scraper registry - maps scraper class names to implementations.

HOW THE REGISTRY WORKS:
  1. Each scraper has a unique string key (e.g., "greenhouse", "lever")
  2. The JobSource model stores this key in its `scraper_class` column
  3. When it's time to scrape, we look up the key → get the class → instantiate it
  4. This is the Strategy Pattern - swap scraping logic without changing the pipeline

WHY a registry instead of if/elif chains?
  - Open for extension: add a new scraper = add one import + one dict entry
  - No modification to existing code (Open/Closed Principle)
  - Easy to list all available scrapers for admin UI
"""
from typing import Dict, Type

from app.scrapers.base import BaseScraper

# ─── Import scrapers ──────────────────────────────────────────────
# Static HTML scrapers (fragile, break when sites redesign)
from app.scrapers.companies.safaricom import SafaricomCareersScraper

# API-based scrapers (stable, structured JSON responses)
from app.scrapers.companies.greenhouse import GreenhouseAPIScraper
from app.scrapers.companies.lever import LeverAPIScraper
from app.scrapers.companies.remotive import RemotiveAPIScraper


# ─── Registry ─────────────────────────────────────────────────────
# Key = what we store in JobSource.scraper_class
# Value = the Python class to instantiate
#
# To add a new scraper:
#   1. Create the class in app/scrapers/companies/
#   2. Import it above
#   3. Add one line here
SCRAPER_REGISTRY: Dict[str, Type[BaseScraper]] = {
    # HTML scrapers
    "safaricom_careers": SafaricomCareersScraper,

    # ATS API scrapers (one class handles MANY companies)
    "greenhouse": GreenhouseAPIScraper,
    "lever": LeverAPIScraper,

    # Aggregator scrapers (one source → jobs from many companies)
    "remotive": RemotiveAPIScraper,
}


def get_scraper(
    scraper_class: str,
    source_id: str,
    config: dict = None,
) -> BaseScraper:
    """
    Get a scraper instance by class name.

    Args:
        scraper_class: Name of the scraper class (e.g., 'greenhouse')
        source_id: UUID of the job source
        config: Scraper-specific configuration (e.g., {"board_slug": "twilio"})

    Returns:
        Scraper instance

    Raises:
        ValueError: If scraper class is not registered
    """
    if scraper_class not in SCRAPER_REGISTRY:
        raise ValueError(f"Unknown scraper class: {scraper_class}")

    scraper_cls = SCRAPER_REGISTRY[scraper_class]
    return scraper_cls(source_id, config)


def list_scrapers() -> list:
    """
    List all registered scraper classes.

    Returns:
        List of scraper class names
    """
    return list(SCRAPER_REGISTRY.keys())
