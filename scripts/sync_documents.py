"""Sync Confluence / Notion / SharePoint / Google Drive into the knowledge base.

python -m scripts.sync_documents --as admin@company.com            # every configured source
python -m scripts.sync_documents --as admin@company.com --source confluence

Runs as the given user (needs knowledge:manage), with settings from .env. Schedule it
(cron, Kubernetes CronJob) to keep the knowledge base current.
"""

import argparse
import asyncio
import sys

from sqlalchemy import select

from backend.app.core.config import Settings
from backend.app.core.security.permissions import Principal
from backend.app.db.models import User
from backend.app.db.session import Database
from backend.app.domains.knowledge.embeddings import build_embedder
from backend.app.domains.knowledge.sync import sync_source
from backend.app.integrations.hub import build_integration_hub


async def run(email: str, source: str | None) -> int:
    settings = Settings()
    hub = build_integration_hub(settings)
    sources = hub.by_category("docs")
    if source:
        sources = {source: sources[source]} if source in sources else {}
    if not sources:
        print("No matching document source is configured (see python -m scripts.connections).")
        return 1
    database = Database(settings.database_url)
    embedder = build_embedder(settings)
    try:
        async with database.session() as session:
            user = await session.scalar(select(User).where(User.email == email.lower()))
            if user is None or not user.is_active:
                print(f"No active user {email}")
                return 1
            principal = Principal.human(user.id, user.email, user.role)
            failed = False
            for name, service in sources.items():
                result = await sync_source(session, settings, embedder, principal, name, service)
                failed |= bool(result.error)
                print(
                    f"{name}: +{result.added} ~{result.updated} "
                    f"={result.unchanged} -{result.removed}"
                    + (f"  error: {result.error}" if result.error else "")
                    + (f"  skipped {len(result.skipped)}" if result.skipped else "")
                )
            return 1 if failed else 0
    finally:
        if embedder is not None:
            await embedder.close()
        await hub.close()
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--as", dest="email", required=True, help="user with knowledge:manage")
    parser.add_argument("--source", help="one source, e.g. confluence")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.exit(asyncio.run(run(args.email, args.source)))


if __name__ == "__main__":
    main()
