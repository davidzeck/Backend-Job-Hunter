"""
Lever ATS API scraper.

Lever is another major ATS, used by companies like Netflix, Figma, Shopify.
Like Greenhouse, they expose a PUBLIC JSON API per company - no auth needed.

API docs: https://github.com/lever/postings-api

How it works:
  - Every company on Lever has a slug (e.g., "netflix", "figma")
  - The API returns all open postings for that company
  - We store the slug in the source's config column

Example API call:
  GET https://api.lever.co/v0/postings/netflix?mode=json

KEY DIFFERENCE from Greenhouse:
  - Lever returns a flat array (not wrapped in {"jobs": [...]})
  - Lever uses epoch milliseconds for dates (not ISO 8601)
  - Lever has a `workplaceType` field (Greenhouse doesn't)
  - Lever's `categories` object has location, commitment, team, etc.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from html import unescape
import re

from app.scrapers.base import APIScraper, ScrapedJob


class LeverAPIScraper(APIScraper):
    """
    Scraper for companies using Lever ATS.

    Config required:
        company_slug: str - The company's Lever slug (e.g., "netflix")

    Optional config:
        team_filter: str - Only return jobs from this team
        location_filter: str - Only return jobs matching this location
    """

    API_BASE = "https://api.lever.co/v0/postings"

    def __init__(self, source_id: str, config: Dict[str, Any] = None):
        super().__init__(source_id, config)
        self.company_slug = self.config.get("company_slug")
        if not self.company_slug:
            raise ValueError("Lever scraper requires 'company_slug' in config")

    def get_source_url(self) -> str:
        return f"{self.API_BASE}/{self.company_slug}"

    async def scrape(self) -> List[ScrapedJob]:
        """
        Fetch all open postings from a Lever board.

        The API returns a top-level array (NOT wrapped in an object):
        [
            {
                "id": "abc123-def456",
                "text": "Software Engineer",
                "hostedUrl": "https://jobs.lever.co/company/abc123",
                "applyUrl": "https://jobs.lever.co/company/abc123/apply",
                "categories": {
                    "location": "Nairobi, Kenya",
                    "commitment": "Full-time",
                    "team": "Engineering",
                    "department": "Product"
                },
                "description": "We are looking for...",
                "descriptionPlain": "We are looking for...",
                "lists": [
                    {"text": "Requirements", "content": "<li>5+ years...</li>"}
                ],
                "createdAt": 1705305600000,
                "workplaceType": "remote"
            }
        ]

        Notice: createdAt is epoch MILLISECONDS, not seconds.
        """
        # mode=json tells Lever to return JSON instead of their embed format
        data = await self.fetch_json(
            self.get_source_url(),
            params={"mode": "json"},
        )

        # Lever returns a flat array, not {"jobs": [...]}
        if not isinstance(data, list):
            return []

        jobs = []
        team_filter = self.config.get("team_filter", "").lower()
        location_filter = self.config.get("location_filter", "").lower()

        for raw_posting in data:
            categories = raw_posting.get("categories", {})

            # Optional: filter by team
            if team_filter:
                team = (categories.get("team") or "").lower()
                if team_filter not in team:
                    continue

            # Optional: filter by location
            if location_filter:
                loc = (categories.get("location") or "").lower()
                if location_filter not in loc:
                    continue

            job = self._map_posting(raw_posting)
            if job:
                jobs.append(job)

        return jobs

    def _map_posting(self, raw: Dict[str, Any]) -> Optional[ScrapedJob]:
        """
        Map a Lever posting to our ScrapedJob dataclass.
        """
        title = raw.get("text", "").strip()
        if not title:
            return None

        categories = raw.get("categories", {})

        # Location from categories
        location = categories.get("location", "")

        # Lever has a dedicated workplaceType field
        workplace_type = raw.get("workplaceType", "")
        location_type = self._map_workplace_type(workplace_type, location)

        # Commitment -> job_type mapping
        commitment = categories.get("commitment", "")
        job_type = self._map_commitment(commitment)

        # Description: Lever gives us both HTML and plain text
        # Use plain text if available, otherwise strip HTML
        description = raw.get("descriptionPlain") or self._strip_html(
            raw.get("description", "")
        )

        # Append list sections (Requirements, Qualifications, etc.)
        for section in raw.get("lists", []):
            section_title = section.get("text", "")
            section_content = self._strip_html(section.get("content", ""))
            if section_content:
                description += f"\n\n{section_title}:\n{section_content}"

        # Seniority from title
        seniority = self._infer_seniority(title)

        # Parse epoch milliseconds -> datetime
        posted_at = self._parse_epoch_ms(raw.get("createdAt"))

        return ScrapedJob(
            external_id=raw["id"],
            title=title,
            description=description.strip(),
            location=location,
            location_type=location_type,
            job_type=job_type,
            seniority_level=seniority,
            apply_url=raw.get("applyUrl") or raw.get("hostedUrl", ""),
            posted_at=posted_at,
            raw_data={
                "team": categories.get("team"),
                "department": categories.get("department"),
                "commitment": commitment,
                "workplace_type": workplace_type,
            },
        )

    # ─── Helper methods ───────────────────────────────────────────

    @staticmethod
    def _map_workplace_type(workplace_type: str, location: str) -> str:
        """
        Map Lever's workplaceType to our location_type enum.

        Lever uses: "remote", "onsite", "hybrid", or sometimes empty.
        """
        wt = workplace_type.lower()
        if wt == "remote":
            return "remote"
        if wt == "hybrid":
            return "hybrid"
        if wt == "onsite":
            return "onsite"
        # Fallback: check location string
        loc_lower = location.lower()
        if "remote" in loc_lower:
            return "remote"
        return "onsite"

    @staticmethod
    def _map_commitment(commitment: str) -> str:
        """
        Map Lever's commitment field to our job_type enum.

        Lever uses: "Full-time", "Part-time", "Contract", "Intern", etc.
        """
        c = commitment.lower()
        if "full" in c:
            return "full_time"
        if "part" in c:
            return "part_time"
        if "contract" in c or "freelance" in c:
            return "contract"
        if "intern" in c:
            return "internship"
        return "full_time"

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
    def _parse_epoch_ms(epoch_ms: Optional[int]) -> Optional[datetime]:
        """
        Convert epoch milliseconds to datetime.

        Lever stores timestamps as Unix epoch in milliseconds.
        Python's datetime.fromtimestamp() expects seconds.
        """
        if not epoch_ms:
            return None
        try:
            return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
        except (ValueError, OSError):
            return None
