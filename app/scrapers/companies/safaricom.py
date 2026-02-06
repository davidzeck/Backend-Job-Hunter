"""
Safaricom Careers Page Scraper.

Scrapes job listings from Safaricom's careers website.
This is a sample implementation - adjust selectors based on actual site structure.
"""
from typing import List, Optional
from datetime import datetime, timedelta
import re

from app.scrapers.base import StaticScraper, ScrapedJob


class SafaricomCareersScraper(StaticScraper):
    """
    Scraper for Safaricom careers page.

    Note: This is a template implementation. The actual selectors
    need to be adjusted based on the current structure of Safaricom's
    careers website.
    """

    BASE_URL = "https://safaricom.co.ke/careers"
    JOBS_URL = "https://safaricom.co.ke/careers/jobs"

    def get_source_url(self) -> str:
        return self.JOBS_URL

    async def scrape(self) -> List[ScrapedJob]:
        """
        Scrape job listings from Safaricom careers page.
        """
        jobs = []

        # Fetch the jobs page
        html = await self.fetch_page(self.JOBS_URL)
        soup = self.parse_html(html)

        # Find job listings
        # Note: These selectors are examples - adjust based on actual site structure
        job_cards = soup.select(".job-listing, .career-item, .vacancy-item")

        if not job_cards:
            # Try alternative selectors
            job_cards = soup.select("[data-job-id], .job-card, .position-item")

        for card in job_cards:
            try:
                job = self._parse_job_card(card)
                if job:
                    jobs.append(job)
            except Exception as e:
                # Log error but continue with other jobs
                print(f"Error parsing job card: {e}")
                continue

            # Be respectful between parsing
            await self.respectful_delay()

        return jobs

    def _parse_job_card(self, card) -> Optional[ScrapedJob]:
        """
        Parse a single job card element.

        Args:
            card: BeautifulSoup element representing a job card

        Returns:
            ScrapedJob if parsing successful, None otherwise
        """
        # Extract job ID
        external_id = (
            card.get("data-job-id")
            or card.get("id")
            or card.select_one("[data-id]")
        )
        if hasattr(external_id, "get"):
            external_id = external_id.get("data-id")

        if not external_id:
            # Generate ID from title if not available
            title_elem = card.select_one(".job-title, .position-title, h3, h4")
            if title_elem:
                external_id = re.sub(r"\W+", "-", title_elem.text.strip().lower())

        # Extract title
        title_elem = card.select_one(".job-title, .position-title, h3, h4, a")
        title = title_elem.text.strip() if title_elem else None

        if not title:
            return None

        # Extract location
        location_elem = card.select_one(".job-location, .location, [data-location]")
        location = location_elem.text.strip() if location_elem else "Nairobi, Kenya"

        # Determine location type
        location_lower = location.lower() if location else ""
        if "remote" in location_lower:
            location_type = "remote"
        elif "hybrid" in location_lower:
            location_type = "hybrid"
        else:
            location_type = "onsite"

        # Extract description/summary
        desc_elem = card.select_one(".job-description, .summary, .excerpt, p")
        description = desc_elem.text.strip() if desc_elem else None

        # Extract apply URL
        link_elem = card.select_one("a[href*='job'], a[href*='career'], a.apply-btn")
        if link_elem and link_elem.get("href"):
            apply_url = link_elem["href"]
            if not apply_url.startswith("http"):
                apply_url = f"https://safaricom.co.ke{apply_url}"
        else:
            apply_url = self.JOBS_URL

        # Extract posted date if available
        date_elem = card.select_one(".posted-date, .date, time")
        posted_at = None
        if date_elem:
            try:
                date_text = date_elem.get("datetime") or date_elem.text.strip()
                posted_at = self._parse_date(date_text)
            except Exception:
                pass

        return ScrapedJob(
            external_id=str(external_id),
            title=title,
            description=description,
            location=location,
            location_type=location_type,
            apply_url=apply_url,
            posted_at=posted_at,
            raw_data={"html": str(card)[:500]},  # Store first 500 chars for debugging
        )

    def _parse_date(self, date_text: str) -> Optional[datetime]:
        """
        Parse various date formats.

        Args:
            date_text: Date string to parse

        Returns:
            datetime object or None
        """
        date_text = date_text.lower().strip()

        # Handle relative dates
        if "today" in date_text:
            return datetime.now()
        elif "yesterday" in date_text:
            return datetime.now() - timedelta(days=1)
        elif "days ago" in date_text:
            match = re.search(r"(\d+)\s*days?\s*ago", date_text)
            if match:
                days = int(match.group(1))
                return datetime.now() - timedelta(days=days)

        # Try common date formats
        formats = [
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%d-%m-%Y",
            "%B %d, %Y",
            "%b %d, %Y",
            "%d %B %Y",
            "%d %b %Y",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(date_text, fmt)
            except ValueError:
                continue

        return None
