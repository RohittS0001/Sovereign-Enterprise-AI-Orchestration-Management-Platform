"""Create (or promote) the first administrator account.

Run migrations first, then:
    python -m scripts.create_admin --email admin@example.com --name "Platform Admin"

The password is read from the ADMIN_PASSWORD environment variable or prompted for, so
it never appears in shell history.
"""

import argparse
import asyncio
import getpass
import os
import sys

from pydantic import ValidationError
from sqlalchemy import select

from backend.app.core.config.settings import get_settings
from backend.app.core.security.passwords import hash_password
from backend.app.core.security.permissions import Role
from backend.app.db.models import User
from backend.app.db.session import Database
from backend.app.schemas.auth import UserCreate


async def create_admin(email: str, name: str, password: str) -> str:
    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not set.")

    data = UserCreate(email=email, full_name=name, password=password, role=Role.ADMIN)
    database = Database(settings.database_url)
    try:
        async with database.session() as session:
            user = await session.scalar(select(User).where(User.email == data.email))
            if user is None:
                session.add(
                    User(
                        email=data.email,
                        full_name=data.full_name,
                        hashed_password=hash_password(data.password),
                        role=Role.ADMIN,
                    )
                )
                outcome = "created"
            else:
                user.role = Role.ADMIN
                user.is_active = True
                user.hashed_password = hash_password(data.password)
                outcome = "updated"
            await session.commit()
    finally:
        await database.dispose()
    return outcome


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()

    password = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Admin password: ")
    try:
        outcome = asyncio.run(create_admin(args.email, args.name, password))
    except ValidationError as exc:
        print(f"Invalid input: {exc.errors()[0]['msg']}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"Admin account {outcome}: {args.email.lower()}")


if __name__ == "__main__":
    main()
