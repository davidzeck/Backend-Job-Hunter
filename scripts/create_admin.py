"""
Create or promote an admin user.

Usage:
    python -m scripts.create_admin admin@example.com                # promote existing / prompt for password
    python -m scripts.create_admin admin@example.com --password S3cret!pw

WHY a script (not startup magic)?
- Explicit and auditable: someone deliberately ran it on a specific host
- Idempotent: promoting an existing admin is a no-op
- Works for both bootstrap (create) and promotion (existing account)
"""
import argparse
import asyncio
import getpass
import os
import sys

# Add project root to path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.core.database import async_session_maker, init_db
from app.core.security import hash_password
from app.models.user import User


async def create_admin(email: str, password: str | None) -> None:
    await init_db()
    async with async_session_maker() as db:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if user:
            if user.is_admin:
                print(f"✓ {email} is already an admin — nothing to do")
                return
            user.is_admin = True
            user.is_active = True
            await db.commit()
            print(f"✓ Promoted existing user {email} to admin")
            return

        if not password:
            password = getpass.getpass(f"Password for new admin {email}: ")
        if len(password) < 8:
            print("✗ Password must be at least 8 characters")
            sys.exit(1)

        user = User(
            email=email,
            password_hash=hash_password(password),
            full_name="Administrator",
            is_admin=True,
            is_active=True,
            email_verified=True,
        )
        db.add(user)
        await db.commit()
        print(f"✓ Created admin user {email}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or promote an admin user")
    parser.add_argument("email", help="Admin email address")
    parser.add_argument("--password", help="Password (prompted if omitted for a new user)")
    args = parser.parse_args()
    asyncio.run(create_admin(args.email, args.password))


if __name__ == "__main__":
    main()
