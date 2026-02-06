"""
Scraper registry - maps scraper class names to implementations.
"""
from typing import Dict, Type

from app.scrapers.base import BaseScraper

# Import company scrapers
from app.scrapers.companies.safaricom import SafaricomCareersScraper
# from app.scrapers.companies.google import GoogleCareersScraper
# from app.scrapers.companies.microsoft import MicrosoftCareersScraper
# from app.scrapers.companies.amazon import AmazonJobsScraper
# from app.scrapers.companies.deloitte import DeloitteCareersScraper
# from app.scrapers.companies.indeed import IndeedSearchScraper
# from app.scrapers.companies.glassdoor import GlassdoorSearchScraper


# Registry mapping scraper class names to implementations
SCRAPER_REGISTRY: Dict[str, Type[BaseScraper]] = {
    "safaricom_careers": SafaricomCareersScraper,
    # "google_careers": GoogleCareersScraper,
    # "microsoft_careers": MicrosoftCareersScraper,
    # "amazon_jobs": AmazonJobsScraper,
    # "deloitte_careers": DeloitteCareersScraper,
    # "indeed_search": IndeedSearchScraper,
    # "glassdoor_search": GlassdoorSearchScraper,
}


def get_scraper(
    scraper_class: str,
    source_id: str,
    config: dict = None,
) -> BaseScraper:
    """
    Get a scraper instance by class name.

    Args:
        scraper_class: Name of the scraper class (e.g., 'safaricom_careers')
        source_id: UUID of the job source
        config: Scraper-specific configuration

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
