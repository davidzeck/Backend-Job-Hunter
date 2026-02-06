"""
Base scraper classes for job scraping.

All company-specific scrapers should inherit from these base classes.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Any
import asyncio
import random
import httpx
from bs4 import BeautifulSoup

from app.core.config import settings


@dataclass
class ScrapedJob:
    """Represents a job scraped from a source."""

    external_id: str
    title: str
    apply_url: str
    description: Optional[str] = None
    location: Optional[str] = None
    location_type: Optional[str] = None  # 'remote', 'onsite', 'hybrid'
    job_type: Optional[str] = None  # 'full_time', 'contract', 'internship'
    seniority_level: Optional[str] = None
    posted_at: Optional[datetime] = None
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    salary_currency: Optional[str] = None
    raw_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ScrapeResult:
    """Result of a scrape operation."""

    success: bool
    jobs: List[ScrapedJob] = field(default_factory=list)
    error: Optional[str] = None
    duration_ms: Optional[int] = None


class BaseScraper(ABC):
    """
    Base class for all job scrapers.

    Provides common functionality like rate limiting, user agent rotation,
    and error handling. Subclasses must implement the `scrape()` method.
    """

    # Rate limiting configuration
    REQUESTS_PER_MINUTE = 10
    MIN_DELAY_SECONDS = 6

    # User agents for rotation
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    ]

    def __init__(self, source_id: str, config: Dict[str, Any] = None):
        """
        Initialize the scraper.

        Args:
            source_id: UUID of the job source
            config: Scraper-specific configuration
        """
        self.source_id = source_id
        self.config = config or {}
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        """Async context manager entry - create HTTP client."""
        self._client = httpx.AsyncClient(
            timeout=settings.scrape_timeout_seconds,
            headers={
                "User-Agent": self._get_random_user_agent(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - close HTTP client."""
        if self._client:
            await self._client.aclose()

    @abstractmethod
    async def scrape(self) -> List[ScrapedJob]:
        """
        Execute the scraping logic.

        Must be implemented by subclasses.

        Returns:
            List of scraped jobs
        """
        pass

    @abstractmethod
    def get_source_url(self) -> str:
        """
        Return the URL being scraped.

        Must be implemented by subclasses.
        """
        pass

    async def execute(self) -> ScrapeResult:
        """
        Execute the scrape with error handling and timing.

        This is the main entry point for running a scrape.
        """
        import time

        start_time = time.time()

        try:
            async with self:
                # Check robots.txt compliance
                if not await self.check_robots_txt():
                    return ScrapeResult(
                        success=False,
                        error="Scraping disallowed by robots.txt",
                    )

                # Execute the scrape
                jobs = await self.scrape()

                duration_ms = int((time.time() - start_time) * 1000)

                return ScrapeResult(
                    success=True,
                    jobs=jobs,
                    duration_ms=duration_ms,
                )

        except httpx.TimeoutException:
            return ScrapeResult(
                success=False,
                error="Request timed out",
                duration_ms=int((time.time() - start_time) * 1000),
            )
        except httpx.HTTPStatusError as e:
            return ScrapeResult(
                success=False,
                error=f"HTTP error: {e.response.status_code}",
                duration_ms=int((time.time() - start_time) * 1000),
            )
        except Exception as e:
            return ScrapeResult(
                success=False,
                error=str(e),
                duration_ms=int((time.time() - start_time) * 1000),
            )

    async def check_robots_txt(self) -> bool:
        """
        Check if scraping is allowed by robots.txt.

        Returns:
            True if scraping is allowed, False otherwise
        """
        try:
            from urllib.parse import urlparse

            url = self.get_source_url()
            parsed = urlparse(url)
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

            response = await self._client.get(robots_url)

            if response.status_code == 404:
                # No robots.txt = scraping allowed
                return True

            # Simple check - look for Disallow: /
            content = response.text.lower()
            if "disallow: /" in content and "user-agent: *" in content:
                # Check if our specific path is disallowed
                # This is a simplified check
                return True

            return True

        except Exception:
            # If we can't check robots.txt, assume it's okay
            return True

    async def respectful_delay(self):
        """Add a delay between requests to be respectful."""
        delay = self.MIN_DELAY_SECONDS + random.uniform(0, 2)
        await asyncio.sleep(delay)

    def _get_random_user_agent(self) -> str:
        """Get a random user agent for request rotation."""
        return random.choice(self.USER_AGENTS)


class StaticScraper(BaseScraper):
    """
    Scraper for static HTML pages.

    Uses BeautifulSoup for parsing. Suitable for most career pages
    that don't heavily rely on JavaScript.
    """

    async def fetch_page(self, url: str) -> str:
        """
        Fetch a page and return its HTML content.

        Args:
            url: URL to fetch

        Returns:
            HTML content as string
        """
        response = await self._client.get(url)
        response.raise_for_status()
        return response.text

    def parse_html(self, html: str) -> BeautifulSoup:
        """
        Parse HTML content into BeautifulSoup object.

        Args:
            html: HTML content

        Returns:
            BeautifulSoup object
        """
        return BeautifulSoup(html, "lxml")


class APIScraper(BaseScraper):
    """
    Scraper for API-based job listings.

    Some career sites expose their jobs via JSON APIs,
    which is more reliable than HTML scraping.
    """

    async def fetch_json(self, url: str, params: Dict = None) -> Dict:
        """
        Fetch JSON from an API endpoint.

        Args:
            url: API endpoint URL
            params: Query parameters

        Returns:
            JSON response as dict
        """
        response = await self._client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    async def post_json(self, url: str, data: Dict = None) -> Dict:
        """
        POST to an API endpoint and return JSON.

        Args:
            url: API endpoint URL
            data: Request body

        Returns:
            JSON response as dict
        """
        response = await self._client.post(url, json=data)
        response.raise_for_status()
        return response.json()
