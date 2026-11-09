"""Load the demo knowledge-base documents (idempotent).

Run migrations and create an admin first, then:
    python -m scripts.seed_demo_knowledge --admin-email admin@example.com
"""

import argparse
import asyncio

from sqlalchemy import select

from backend.app.core.config import get_settings
from backend.app.core.errors import ConflictError
from backend.app.core.security.permissions import Principal, Role
from backend.app.db.models import User, Visibility
from backend.app.db.session import Database
from backend.app.domains.knowledge.embeddings import build_embedder
from backend.app.domains.knowledge.service import KnowledgeService
from backend.app.integrations.demo.seed import DEMO_DOCUMENTS, read_document


async def seed(admin_email: str) -> list[str]:
    settings = get_settings()
    database = Database(settings.database_url)
    embedder = build_embedder(settings)
    outcomes = []
    try:
        async with database.session() as session:
            admin = await session.scalar(select(User).where(User.email == admin_email.lower()))
            if admin is None or admin.role != Role.ADMIN:
                raise SystemExit(f"No admin user {admin_email}; run scripts.create_admin first")
            principal = Principal.human(admin.id, admin.email, admin.role)
            for filename, title, visibility, roles in DEMO_DOCUMENTS:
                text = read_document(filename)
                try:
                    await KnowledgeService(session, settings, embedder).ingest(
                        principal, title=title, text=text, source_type="demo",
                        filename=filename, mime_type="text/markdown",
                        visibility=Visibility(visibility), allowed_roles=roles, tags=["demo"],
                    )  # fmt: skip
                    outcomes.append(f"added: {title}")
                except ConflictError:
                    await session.rollback()
                    outcomes.append(f"exists: {title}")
    finally:
        if embedder is not None:
            await embedder.close()
        await database.dispose()
    return outcomes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--admin-email", required=True)
    for line in asyncio.run(seed(parser.parse_args().admin_email)):
        print(line)


if __name__ == "__main__":
    main()
