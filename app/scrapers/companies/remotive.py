"""
Remotive API scraper.

Remotive is a JOB AGGREGATOR (not an ATS like Greenhouse/Lever).
It collects remote job listings from many companies into one API.

API docs: https://remotive.com/api-documentation

How it works:
  - Single endpoint returns remote jobs across all companies
  - Filter by category (software-dev, design, marketing, etc.)
  - No authentication required (free public API)
  - Rate limited to ~60 requests/minute

Example API call:
  GET https://remotive.com/api/remote-jobs?category=software-dev&limit=50

KEY DIFFERENCE from Greenhouse/Lever:
  - This is an AGGREGATOR - one source returns jobs from MANY companies
  - The company_name comes from the API response, not our config
  - Jobs are always remote (it's a remote-only board)
  - Has salary info (Greenhouse/Lever usually don't)

WHY include an aggregator?
  - Greenhouse/Lever only work if we know which companies to track
  - An aggregator catches jobs from companies we haven't configured
  - Good for discovering new companies to add individual scrapers for
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from html import unescape
import re

from app.scrapers.base import APIScraper, ScrapedJob


class RemotiveAPIScraper(APIScraper):
    """
    Scraper for the Remotive remote jobs API.

    Config optional:
        category: str - Job category filter (default: "software-dev")
            Options: software-dev, design, marketing, sales, customer-support,
                     devops, finance, product, data, hr, all
        limit: int - Max jobs to fetch per scrape (default: 50, max: 100)
        search: str - Search term to filter by (e.g., "python", "react")
    """

    API_URL = "https://remotive.com/api/remote-jobs"

    def get_source_url(self) -> str:
        return self.API_URL

    async def scrape(self) -> List[ScrapedJob]:
        """
        Fetch remote job listings from Remotive.

        The API returns:
        {
            "job-count": 1234,
            "jobs": [
                {
                    "id": 12345,
                    "url": "https://remotive.com/remote-jobs/software-dev/...",
                    "title": "Senior Backend Engineer",
                    "company_name": "Acme Corp",
                    "company_logo": "https://...",
                    "category": "Software Development",
                    "tags": ["python", "django", "postgresql"],
                    "job_type": "full_time",
                    "publication_date": "2024-01-15T00:00:00",
                    "candidate_required_location": "Worldwide",
                    "salary": "$80,000 - $120,000",
                    "description": "<p>We are looking for...</p>"
                }
            ]
        }
        """
        params = {
            "category": self.config.get("category", "software-dev"),
            "limit": min(self.config.get("limit", 50), 100),
        }

        # Optional search filter
        search = self.config.get("search")
        if search:
            params["search"] = search

        data = await self.fetch_json(self.API_URL, params=params)

        jobs = []
        for raw_job in data.get("jobs", []):
            job = self._map_job(raw_job)
            if job:
                jobs.append(job)

        return jobs

    def _map_job(self, raw: Dict[str, Any]) -> Optional[ScrapedJob]:
        """
        Map a Remotive job to our ScrapedJob dataclass.
        """
        title = raw.get("title", "").strip()
        if not title:
            return None

        # Location: Remotive uses "candidate_required_location"
        # Values like "Worldwide", "USA Only", "Europe", "EMEA"
        location = raw.get("candidate_required_location", "Remote")
        if not location:
            location = "Remote"

        # All Remotive jobs are remote by definition
        location_type = "remote"

        # Description: HTML content
        description = self._strip_html(raw.get("description", ""))

        # Job type: Remotive provides this directly
        job_type = self._map_job_type(raw.get("job_type", ""))

        # Seniority from title
        seniority = self._infer_seniority(title)

        # Parse salary string (e.g., "$80,000 - $120,000")
        salary_min, salary_max, currency = self._parse_salary(
            raw.get("salary", "")
        )

        # Parse publication date
        posted_at = self._parse_date(raw.get("publication_date"))

        return ScrapedJob(
            external_id=str(raw["id"]),
            title=title,
            description=description,
            location=location,
            location_type=location_type,
            job_type=job_type,
            seniority_level=seniority,
            apply_url=raw.get("url", ""),
            posted_at=posted_at,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=currency,
            raw_data={
                "company_name": raw.get("company_name"),
                "company_logo": raw.get("company_logo"),
                "category": raw.get("category"),
                "tags": raw.get("tags", []),
            },
        )

    # ─── Helper methods ───────────────────────────────────────────

    @staticmethod
    def _strip_html(html: str) -> str:
        """Remove HTML tags and decode entities."""
        if not html:
            return ""
        text = re.sub(r"<[^>]+>", " ", html)
        text = unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _map_job_type(job_type: str) -> str:
        """
        Map Remotive's job_type to our enum.

        Remotive uses: "full_time", "contract", "part_time", "freelance",
                       "internship", "other"
        """
        jt = job_type.lower().replace("-", "_").replace(" ", "_")
        if "full" in jt:
            return "full_time"
        if "part" in jt:
            return "part_time"
        if "contract" in jt or "freelance" in jt:
            return "contract"
        if "intern" in jt:
            return "internship"
        return "full_time"

    @staticmethod
    def _infer_seniority(title: str) -> Optional[str]:
        """Infer seniority level from the job title."""
        title_lower = title.lower()
        if any(w in title_lower for w in ("intern", "internship")):
            return "intern"
        if "junior" in title_lower or "entry" in title_lower:
            return "junior"
        if "senior" in title_lower or "sr." in title_lower or "sr " in title_lower:
            return "senior"
        if any(w in title_lower for w in ("staff", "principal", "distinguished")):
            return "staff"
        if any(w in title_lower for w in ("lead", "manager", "head of", "director", "vp")):
            return "lead"
        return "mid"

    @staticmethod
    def _parse_salary(salary_str: str) -> tuple:
        """
        Parse salary string into (min, max, currency).

        Examples:
            "$80,000 - $120,000" -> (80000, 120000, "USD")
            "€50,000-€70,000"   -> (50000, 70000, "EUR")
            ""                  -> (None, None, None)
        """
        if not salary_str:
            return None, None, None

        # Detect currency
        currency = "USD"  # default
        if "€" in salary_str or "EUR" in salary_str.upper():
            currency = "EUR"
        elif "£" in salary_str or "GBP" in salary_str.upper():
            currency = "GBP"
        elif "KES" in salary_str.upper() or "KSh" in salary_str:
            currency = "KES"

        # Extract numbers
        numbers = re.findall(r"[\d,]+", salary_str)
        numbers = [int(n.replace(",", "")) for n in numbers if n]

        if len(numbers) >= 2:
            return min(numbers), max(numbers), currency
        elif len(numbers) == 1:
            return numbers[0], numbers[0], currency
        return None, None, None

    @staticmethod
    def _parse_date(date_str: Optional[str]) -> Optional[datetime]:
        """Parse ISO date string from Remotive."""
        if not date_str:
            return None
        try:
            # Remotive format: "2024-01-15T00:00:00"
            dt = datetime.fromisoformat(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None
