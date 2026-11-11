"""Sync Confluence / Notion / SharePoint / Google Drive into the knowledge base.

python -m scripts.sync_documents --as admin@company.com            # every configured source
python -m scripts.sync_documents --as admin@company.com --source confluence
python -m scripts.sync_documents --as admin@company.com --projects   # + index every connected
                                                                     #   platform per project

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


async def run(email: str, source: str | None, projects: bool = False) -> int:
    settings = Settings()
    hub = build_integration_hub(settings)
    sources = hub.by_category("docs")
    if source:
        sources = {source: sources[source]} if source in sources else {}
    if not sources and not projects:
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
            if projects:
                failed |= await _index_projects(session, settings, embedder, principal, hub)
            return 1 if failed else 0
    finally:
        if embedder is not None:
            await embedder.close()
        await hub.close()
        await database.dispose()


async def _index_projects(session, settings, embedder, principal, hub) -> bool:
    from backend.app.db.models import Project
    from backend.app.domains.analytics.collector import SnapshotCollector
    from backend.app.domains.knowledge.indexing import index_project

    collector = SnapshotCollector(hub, settings)
    failed = False
    for project in await session.scalars(select(Project).where(Project.is_active.is_(True))):
        snapshot = await collector.collect(project, fresh=True)
        result = await index_project(session, settings, embedder, principal, hub, project, snapshot)
        for s in result.sources:
            failed |= bool(s.error)
            print(
                f"{project.key}/{s.source}: +{s.added} ~{s.updated} ={s.unchanged} -{s.removed}"
                + (f"  error: {s.error}" if s.error else "")
            )
    return failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--as", dest="email", required=True, help="user with knowledge:manage")
    parser.add_argument("--source", help="one source, e.g. confluence")
    parser.add_argument(
        "--projects",
        action="store_true",
        help="also index Jira, GitHub, chat, e-mail, ops, HR … per active project",
    )
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.exit(asyncio.run(run(args.email, args.source, args.projects)))


if __name__ == "__main__":
    main()
