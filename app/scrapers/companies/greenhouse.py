"""
Greenhouse ATS API scraper.

Greenhouse is an Applicant Tracking System (ATS) used by hundreds of companies.
They expose a PUBLIC JSON API for each company's job board - no auth needed.

API docs: https://developers.greenhouse.io/job-board.html

How it works:
  - Every company on Greenhouse has a "board slug" (e.g., "twilio", "airbnb")
  - The API returns all open jobs for that company
  - We store the board_slug in the source's config column

Example API call:
  GET https://boards-api.greenhouse.io/v1/boards/twilio/jobs?content=true

WHY Greenhouse is a great scraping target:
  - Public API, no authentication required
  - Stable schema (rarely changes)
  - Returns structured data (title, location, content, departments)
  - Used by hundreds of tech companies
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from html import unescape
import re

from app.scrapers.base import APIScraper, ScrapedJob


class GreenhouseAPIScraper(APIScraper):
    """
    Scraper for companies using Greenhouse ATS.

    Config required:
        board_slug: str - The company's Greenhouse board slug (e.g., "twilio")

    Optional config:
        department_filter: str - Only return jobs from this department
    """

    API_BASE = "https://boards-api.greenhouse.io/v1/boards"

    def __init__(self, source_id: str, config: Dict[str, Any] = None):
        super().__init__(source_id, config)
        self.board_slug = self.config.get("board_slug")
        if not self.board_slug:
            raise ValueError("Greenhouse scraper requires 'board_slug' in config")

    def get_source_url(self) -> str:
        return f"{self.API_BASE}/{self.board_slug}/jobs"

    async def scrape(self) -> List[ScrapedJob]:
        """
        Fetch all open jobs from a Greenhouse board.

        The API returns:
        {
            "jobs": [
                {
                    "id": 123456,
                    "title": "Software Engineer",
                    "absolute_url": "https://boards.greenhouse.io/company/jobs/123456",
                    "location": {"name": "Nairobi, Kenya"},
                    "content": "<p>Job description HTML...</p>",
                    "updated_at": "2024-01-15T12:00:00-05:00",
                    "departments": [{"name": "Engineering"}],
                    "offices": [{"name": "Nairobi"}]
                }
            ],
            "meta": {"total": 42}
        }
        """
        # content=true includes the full job description HTML
        data = await self.fetch_json(
            self.get_source_url(),
            params={"content": "true"},
        )

        jobs = []
        dept_filter = self.config.get("department_filter", "").lower()

        for raw_job in data.get("jobs", []):
            # Optional: filter by department (e.g., only "Engineering" jobs)
            if dept_filter:
                departments = [
                    d["name"].lower() for d in raw_job.get("departments", [])
                ]
                if not any(dept_filter in dept for dept in departments):
                    continue

            job = self._map_job(raw_job)
            if job:
                jobs.append(job)

        return jobs

    def _map_job(self, raw: Dict[str, Any]) -> Optional[ScrapedJob]:
        """
        Map a Greenhouse API job object to our ScrapedJob dataclass.

        This is where the real work happens - translating their schema to ours.
        """
        title = raw.get("title", "").strip()
        if not title:
            return None

        # Location: Greenhouse nests it under {"name": "..."}
        location_data = raw.get("location", {})
        location = location_data.get("name", "") if location_data else ""

        # Infer location type from the location string
        location_type = self._infer_location_type(location)

        # Description: comes as HTML, strip tags for clean text
        description_html = raw.get("content", "")
        description = self._strip_html(description_html)

        # Seniority: infer from the job title
        seniority = self._infer_seniority(title)

        # Parse the updated_at timestamp
        posted_at = self._parse_timestamp(raw.get("updated_at"))

        return ScrapedJob(
            external_id=str(raw["id"]),
            title=title,
            description=description,
            location=location,
            location_type=location_type,
            job_type="full_time",  # Greenhouse doesn't expose this directly
            seniority_level=seniority,
            apply_url=raw.get("absolute_url", ""),
            posted_at=posted_at,
            raw_data={
                "departments": [d["name"] for d in raw.get("departments", [])],
                "offices": [o["name"] for o in raw.get("offices", [])],
            },
        )

    # ─── Helper methods ───────────────────────────────────────────

    @staticmethod
    def _strip_html(html: str) -> str:
        """Remove HTML tags and decode entities. Keep it readable."""
        if not html:
            return ""
        # Remove tags
        text = re.sub(r"<[^>]+>", " ", html)
        # Decode HTML entities (&amp; -> &, etc.)
        text = unescape(text)
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _infer_location_type(location: str) -> str:
        """
        Guess location type from the location string.

        Greenhouse doesn't have a dedicated field for this,
        so we look for keywords in the location name.
        """
        loc_lower = location.lower()
        if "remote" in loc_lower:
            return "remote"
        if "hybrid" in loc_lower:
            return "hybrid"
        return "onsite"

    @staticmethod
    def _infer_seniority(title: str) -> Optional[str]:
        """
        Infer seniority level from the job title.

        Not perfect, but catches the common patterns.
        """
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
    def _parse_timestamp(ts: Optional[str]) -> Optional[datetime]:
        """Parse ISO 8601 timestamp from Greenhouse API."""
        if not ts:
            return None
        try:
            # Python 3.11+ handles timezone offsets natively
            return datetime.fromisoformat(ts)
        except ValueError:
            # Fallback: strip timezone and treat as UTC
            try:
                clean = re.sub(r"[+-]\d{2}:\d{2}$", "", ts)
                return datetime.fromisoformat(clean).replace(tzinfo=timezone.utc)
            except ValueError:
                return None
