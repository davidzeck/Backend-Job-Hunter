"""
Seed script - populates the database with test data for development.

Usage:
    python -m scripts.seed

WHY a seed script?
- Every developer needs sample data to test against
- Manual data entry through /docs is tedious and error-prone
- Seeds are deterministic - everyone has the same test data
- Seeds document what "normal" data looks like for this app

This script is IDEMPOTENT - running it twice won't create duplicates.
It checks for existing data before inserting.
"""
import asyncio
import sys
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

# Add project root to path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import async_session_maker, init_db
from app.core.security import hash_password
from app.models.user import User
from app.models.company import Company
from app.models.job_source import JobSource
from app.models.job import Job
from app.models.job_skill import JobSkill
from app.models.user_skill import UserSkill
from app.models.user_job_alert import UserJobAlert
from sqlalchemy import select


# ─── Test User ─────────────────────────────────────────────────

TEST_USER = {
    "email": "dev@jobscout.com",
    "password": "password123",
    "full_name": "Dev User",
    "phone": "+254700000000",
}

ADMIN_USER = {
    "email": "admin@jobscout.com",
    "password": "admin123",
    "full_name": "Admin User",
    "is_admin": True,
}


# ─── Companies ─────────────────────────────────────────────────
# These are the companies we track. Each one needs at least one JobSource
# to tell our scrapers WHERE and HOW to find their jobs.

COMPANIES = [
    # Local company - HTML scraper (template, needs real selectors)
    {
        "name": "Safaricom",
        "slug": "safaricom",
        "careers_url": "https://safaricom.co.ke/careers",
        "description": "Kenya's leading telecommunications company",
        "logo_url": "https://logo.clearbit.com/safaricom.co.ke",
    },
    # Greenhouse ATS companies (verified active, 2024-2025)
    {
        "name": "Twilio",
        "slug": "twilio",
        "careers_url": "https://boards.greenhouse.io/twilio",
        "description": "Cloud communications platform",
        "logo_url": "https://logo.clearbit.com/twilio.com",
    },
    {
        "name": "Cloudflare",
        "slug": "cloudflare",
        "careers_url": "https://boards.greenhouse.io/cloudflare",
        "description": "Web infrastructure and security company",
        "logo_url": "https://logo.clearbit.com/cloudflare.com",
    },
    {
        "name": "GitLab",
        "slug": "gitlab",
        "careers_url": "https://boards.greenhouse.io/gitlab",
        "description": "DevOps platform, all-remote company",
        "logo_url": "https://logo.clearbit.com/gitlab.com",
    },
    {
        "name": "Airtable",
        "slug": "airtable",
        "careers_url": "https://boards.greenhouse.io/airtable",
        "description": "Low-code platform for building apps",
        "logo_url": "https://logo.clearbit.com/airtable.com",
    },
    # Lever ATS companies (verified active)
    {
        "name": "Spotify",
        "slug": "spotify",
        "careers_url": "https://jobs.lever.co/spotify",
        "description": "Music streaming platform",
        "logo_url": "https://logo.clearbit.com/spotify.com",
    },
    {
        "name": "Plaid",
        "slug": "plaid",
        "careers_url": "https://jobs.lever.co/plaid",
        "description": "Fintech infrastructure for financial services",
        "logo_url": "https://logo.clearbit.com/plaid.com",
    },
    # Aggregator - catches remote jobs from companies we don't track individually
    {
        "name": "Remotive",
        "slug": "remotive",
        "careers_url": "https://remotive.com",
        "description": "Remote job aggregator - indexes jobs from many companies",
        "logo_url": "https://logo.clearbit.com/remotive.com",
    },
]


# ─── Job Sources ──────────────────────────────────────────────
# This is the KEY mapping: which scraper class + config to use for each company.
#
# Notice how GreenhouseAPIScraper and LeverAPIScraper each handle MULTIPLE
# companies - the only difference is the config (board_slug / company_slug).
# This is the Strategy Pattern in action.

JOB_SOURCES = [
    # Safaricom - HTML scraper (template, will need real CSS selectors)
    {
        "company_slug": "safaricom",
        "source_type": "careers_page",
        "url": "https://safaricom.co.ke/careers/jobs",
        "scraper_class": "safaricom_careers",
        "config": {},
        "scrape_interval_minutes": 60,
    },
    # Greenhouse ATS sources - same scraper class, different board_slug config
    {
        "company_slug": "twilio",
        "source_type": "ats_api",
        "url": "https://boards-api.greenhouse.io/v1/boards/twilio/jobs",
        "scraper_class": "greenhouse",
        "config": {"board_slug": "twilio"},
        "scrape_interval_minutes": 30,
    },
    {
        "company_slug": "cloudflare",
        "source_type": "ats_api",
        "url": "https://boards-api.greenhouse.io/v1/boards/cloudflare/jobs",
        "scraper_class": "greenhouse",
        "config": {"board_slug": "cloudflare"},
        "scrape_interval_minutes": 30,
    },
    {
        "company_slug": "gitlab",
        "source_type": "ats_api",
        "url": "https://boards-api.greenhouse.io/v1/boards/gitlab/jobs",
        "scraper_class": "greenhouse",
        "config": {"board_slug": "gitlab"},
        "scrape_interval_minutes": 30,
    },
    {
        "company_slug": "airtable",
        "source_type": "ats_api",
        "url": "https://boards-api.greenhouse.io/v1/boards/airtable/jobs",
        "scraper_class": "greenhouse",
        "config": {"board_slug": "airtable"},
        "scrape_interval_minutes": 30,
    },
    # Lever ATS sources - same scraper class, different company_slug config
    {
        "company_slug": "spotify",
        "source_type": "ats_api",
        "url": "https://api.lever.co/v0/postings/spotify",
        "scraper_class": "lever",
        "config": {"company_slug": "spotify"},
        "scrape_interval_minutes": 30,
    },
    {
        "company_slug": "plaid",
        "source_type": "ats_api",
        "url": "https://api.lever.co/v0/postings/plaid",
        "scraper_class": "lever",
        "config": {"company_slug": "plaid"},
        "scrape_interval_minutes": 30,
    },
    # Remotive aggregator - catches remote jobs from companies we don't track
    {
        "company_slug": "remotive",
        "source_type": "aggregator",
        "url": "https://remotive.com/api/remote-jobs",
        "scraper_class": "remotive",
        "config": {"category": "software-dev", "limit": 50},
        "scrape_interval_minutes": 60,
    },
]


# ─── Sample Jobs ───────────────────────────────────────────────

SAMPLE_JOBS = [
    # Safaricom - local company, HTML scraper
    {
        "company_slug": "safaricom",
        "title": "Senior Backend Engineer",
        "description": "We are looking for a Senior Backend Engineer to join our M-PESA team. You will design and build scalable microservices handling millions of transactions daily.",
        "location": "Nairobi, Kenya",
        "location_type": "hybrid",
        "job_type": "full_time",
        "seniority_level": "senior",
        "apply_url": "https://safaricom.co.ke/careers/senior-backend-engineer",
        "skills": [
            ("Python", "language", True, 3),
            ("Django", "framework", True, 2),
            ("PostgreSQL", "database", True, 2),
            ("Docker", "tool", False, 1),
            ("Kubernetes", "cloud", False, 1),
        ],
    },
    {
        "company_slug": "safaricom",
        "title": "Frontend Developer",
        "description": "Join our digital products team to build customer-facing web applications for M-PESA and MySafaricom app.",
        "location": "Nairobi, Kenya",
        "location_type": "onsite",
        "job_type": "full_time",
        "seniority_level": "mid",
        "apply_url": "https://safaricom.co.ke/careers/frontend-developer",
        "skills": [
            ("React", "framework", True, 2),
            ("TypeScript", "language", True, 1),
            ("CSS", "language", True, 2),
            ("Next.js", "framework", False, 1),
        ],
    },
    # Twilio - Greenhouse ATS
    {
        "company_slug": "twilio",
        "title": "Software Engineer, Cloud Platform",
        "description": "Design, develop, and test cloud communication APIs used by millions of developers worldwide.",
        "location": "Remote",
        "location_type": "remote",
        "job_type": "full_time",
        "seniority_level": "mid",
        "apply_url": "https://boards.greenhouse.io/twilio",
        "skills": [
            ("Go", "language", True, 2),
            ("Python", "language", True, 2),
            ("Kubernetes", "cloud", True, 1),
            ("gRPC", "framework", False, 1),
            ("Distributed Systems", "concept", True, 2),
        ],
    },
    # Cloudflare - Greenhouse ATS
    {
        "company_slug": "cloudflare",
        "title": "Systems Engineer",
        "description": "Build and maintain edge infrastructure serving millions of requests per second globally.",
        "location": "Remote",
        "location_type": "remote",
        "job_type": "full_time",
        "seniority_level": "senior",
        "apply_url": "https://boards.greenhouse.io/cloudflare",
        "skills": [
            ("Rust", "language", True, 2),
            ("Go", "language", True, 2),
            ("Linux", "tool", True, 3),
            ("Networking", "concept", True, 2),
        ],
    },
    # Figma - Lever ATS
    {
        "company_slug": "figma",
        "title": "Full Stack Engineer",
        "description": "Build features for Figma's collaborative design tool used by millions of designers and developers.",
        "location": "San Francisco, CA",
        "location_type": "hybrid",
        "job_type": "full_time",
        "seniority_level": "mid",
        "apply_url": "https://jobs.lever.co/figma",
        "skills": [
            ("TypeScript", "language", True, 2),
            ("React", "framework", True, 2),
            ("Node.js", "framework", True, 1),
            ("PostgreSQL", "database", False, 1),
        ],
    },
    # Netlify - Lever ATS
    {
        "company_slug": "netlify",
        "title": "Backend Engineer (Remote)",
        "description": "Build the platform that powers web deployments for millions of developers. Fully remote.",
        "location": "Remote",
        "location_type": "remote",
        "job_type": "full_time",
        "seniority_level": "mid",
        "apply_url": "https://jobs.lever.co/netlify",
        "skills": [
            ("Go", "language", True, 2),
            ("TypeScript", "language", True, 1),
            ("PostgreSQL", "database", True, 2),
            ("REST API", "concept", True, 2),
            ("Git", "tool", True, 1),
        ],
    },
    {
        "company_slug": "netlify",
        "title": "DevOps Engineer",
        "description": "Manage cloud infrastructure and CI/CD pipelines for Netlify's global edge network.",
        "location": "Remote",
        "location_type": "remote",
        "job_type": "full_time",
        "seniority_level": "senior",
        "apply_url": "https://jobs.lever.co/netlify",
        "skills": [
            ("AWS", "cloud", True, 3),
            ("Docker", "tool", True, 2),
            ("Kubernetes", "cloud", True, 2),
            ("Terraform", "cloud", True, 2),
            ("CI/CD", "concept", True, 2),
        ],
    },
    # Remotive - aggregator (company is "Remotive" but jobs are from various companies)
    {
        "company_slug": "remotive",
        "title": "Software Engineer - Mobile",
        "description": "Remote mobile engineering position aggregated from Remotive job board.",
        "location": "Worldwide",
        "location_type": "remote",
        "job_type": "full_time",
        "seniority_level": "mid",
        "apply_url": "https://remotive.com/remote-jobs/software-dev",
        "skills": [
            ("Java", "language", True, 2),
            ("Kotlin", "language", False, 1),
            ("Android", "framework", True, 2),
            ("REST API", "concept", True, 1),
        ],
    },
]


# ─── User Skills (for the test user) ──────────────────────────

USER_SKILLS = [
    ("Python", "language", "advanced", 4.0),
    ("FastAPI", "framework", "advanced", 2.0),
    ("PostgreSQL", "database", "intermediate", 3.0),
    ("Docker", "tool", "intermediate", 2.0),
    ("React", "framework", "intermediate", 1.5),
    ("TypeScript", "language", "intermediate", 1.5),
    ("Git", "tool", "advanced", 5.0),
    ("Linux", "tool", "intermediate", 3.0),
]


async def seed():
    """Run the seed process."""
    print("Seeding database...")

    # Initialize tables
    await init_db()
    print("  Tables created")

    async with async_session_maker() as db:

        # ── Users ──────────────────────────────────────────
        existing = await db.execute(select(User).where(User.email == TEST_USER["email"]))
        if existing.scalar_one_or_none():
            print("  Users already exist, skipping...")
            test_user = (await db.execute(
                select(User).where(User.email == TEST_USER["email"])
            )).scalar_one()
        else:
            test_user = User(
                email=TEST_USER["email"],
                password_hash=hash_password(TEST_USER["password"]),
                full_name=TEST_USER["full_name"],
                phone=TEST_USER["phone"],
                email_verified=True,
                fcm_token="fake-fcm-token-for-dev",
            )
            admin_user = User(
                email=ADMIN_USER["email"],
                password_hash=hash_password(ADMIN_USER["password"]),
                full_name=ADMIN_USER["full_name"],
                is_admin=True,
                email_verified=True,
            )
            db.add_all([test_user, admin_user])
            await db.flush()
            print(f"  Created users: {TEST_USER['email']}, {ADMIN_USER['email']}")

        # ── Companies ──────────────────────────────────────
        existing = await db.execute(select(Company).limit(1))
        if existing.scalar_one_or_none():
            print("  Companies already exist, skipping...")
        else:
            company_objects = {}
            for c in COMPANIES:
                company = Company(**c)
                db.add(company)
                company_objects[c["slug"]] = company
            await db.flush()
            print(f"  Created {len(COMPANIES)} companies")

        # Load company map (needed for jobs whether or not we just created them)
        result = await db.execute(select(Company))
        company_map = {c.slug: c for c in result.scalars().all()}

        # ── Job Sources ────────────────────────────────────
        # Each source tells the system: "Use THIS scraper with THIS config
        # to find jobs for THIS company."
        existing = await db.execute(select(JobSource).limit(1))
        if existing.scalar_one_or_none():
            print("  Job sources already exist, skipping...")
        else:
            for src in JOB_SOURCES:
                company = company_map.get(src["company_slug"])
                if not company:
                    print(f"  WARNING: Company '{src['company_slug']}' not found, skipping source")
                    continue
                source = JobSource(
                    company_id=company.id,
                    source_type=src["source_type"],
                    url=src["url"],
                    scraper_class=src["scraper_class"],
                    config=src.get("config", {}),
                    scrape_interval_minutes=src.get("scrape_interval_minutes", 30),
                    health_status="healthy",
                )
                db.add(source)
            await db.flush()
            print(f"  Created {len(JOB_SOURCES)} job sources")

        # Load source map
        result = await db.execute(select(JobSource))
        source_map = {s.company_id: s for s in result.scalars().all()}

        # ── Jobs ───────────────────────────────────────────
        existing = await db.execute(select(Job).limit(1))
        if existing.scalar_one_or_none():
            print("  Jobs already exist, skipping...")
        else:
            now = datetime.now(timezone.utc)
            for i, job_data in enumerate(SAMPLE_JOBS):
                company = company_map[job_data["company_slug"]]
                source = source_map[company.id]

                job = Job(
                    source_id=source.id,
                    company_id=company.id,
                    external_id=f"seed-{job_data['company_slug']}-{i}",
                    title=job_data["title"],
                    description=job_data["description"],
                    location=job_data["location"],
                    location_type=job_data["location_type"],
                    job_type=job_data["job_type"],
                    seniority_level=job_data["seniority_level"],
                    apply_url=job_data["apply_url"],
                    discovered_at=now - timedelta(days=i),  # Stagger discovery dates
                    posted_at=now - timedelta(days=i + 1),
                )
                db.add(job)
                await db.flush()

                # Add skills
                for skill_name, category, required, years in job_data.get("skills", []):
                    skill = JobSkill(
                        job_id=job.id,
                        skill_name=skill_name,
                        skill_category=category,
                        is_required=required,
                        min_years_experience=years,
                    )
                    db.add(skill)

            await db.flush()
            print(f"  Created {len(SAMPLE_JOBS)} jobs with skills")

        # ── User Skills ────────────────────────────────────
        existing = await db.execute(
            select(UserSkill).where(UserSkill.user_id == test_user.id).limit(1)
        )
        if existing.scalar_one_or_none():
            print("  User skills already exist, skipping...")
        else:
            for skill_name, category, proficiency, years in USER_SKILLS:
                skill = UserSkill(
                    user_id=test_user.id,
                    skill_name=skill_name,
                    skill_category=category,
                    proficiency_level=proficiency,
                    years_experience=years,
                    source="manual",
                )
                db.add(skill)
            await db.flush()
            print(f"  Created {len(USER_SKILLS)} user skills")

        # ── Sample Alerts (so the alerts endpoint has data) ─
        existing = await db.execute(
            select(UserJobAlert).where(UserJobAlert.user_id == test_user.id).limit(1)
        )
        if existing.scalar_one_or_none():
            print("  Alerts already exist, skipping...")
        else:
            jobs_result = await db.execute(select(Job).limit(3))
            sample_jobs = jobs_result.scalars().all()

            now = datetime.now(timezone.utc)
            for i, job in enumerate(sample_jobs):
                alert = UserJobAlert(
                    user_id=test_user.id,
                    job_id=job.id,
                    notified_at=now - timedelta(hours=i),
                    notification_channel="push",
                    is_delivered=True,
                    is_read=i > 0,  # First one is unread
                )
                db.add(alert)

            await db.flush()
            print(f"  Created {len(sample_jobs)} sample alerts")

        # Commit everything
        await db.commit()
        print()
        print("Seed complete!")
        print(f"  Login: {TEST_USER['email']} / {TEST_USER['password']}")
        print(f"  Admin: {ADMIN_USER['email']} / {ADMIN_USER['password']}")


if __name__ == "__main__":
    asyncio.run(seed())
