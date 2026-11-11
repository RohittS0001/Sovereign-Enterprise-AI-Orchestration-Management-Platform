"""Run the platform locally in demo mode — no Jira/GitHub/Keka/Gmail credentials needed.

    python -m scripts.run_demo            # API on http://127.0.0.1:8000
    python -m scripts.run_demo --seed-only  # migrate + seed, then exit (Docker)
    cd frontend && npm run dev            # UI on http://localhost:5173

Creates (once) a SQLite database in data/demo.db, one user per role, the demo
knowledge base and the Phoenix project. All demo users share DEMO_PASSWORD
(default below — local use only). Set LLM1_MODEL_NAME (e.g. qwen3:8b on Ollama) to
enable the assistant; everything else works without a model.
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "Demo!Passw0rd123")

# Must be set before any backend module reads settings.
os.environ.setdefault("DEMO_MODE", "true")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(ROOT / 'data' / 'demo.db').as_posix()}")
os.environ.setdefault("JWT_SECRET_KEY", "local-demo-only-secret-change-me-0123456789abcdef")
os.environ.setdefault("LOG_FORMAT", "text")

DEMO_USERS = [
    ("admin@example.com", "Ada Admin", "admin"),
    ("pm@example.com", "Priya N (PM)", "project_manager"),
    ("lead@example.com", "Arjun M (Lead)", "team_lead"),
    ("member@example.com", "Meera S (Member)", "member"),
    ("auditor@example.com", "Ravi A (Auditor)", "auditor"),
]


async def seed() -> None:
    from sqlalchemy import select

    from backend.app.core.config import get_settings
    from backend.app.core.security.passwords import hash_password
    from backend.app.core.security.permissions import Role
    from backend.app.db.models import Project, User
    from backend.app.db.session import Database
    from backend.app.integrations.demo.seed import LDG_CONFIG, PHX_CONFIG
    from scripts.seed_demo_knowledge import seed as seed_knowledge

    database = Database(get_settings().database_url)
    try:
        async with database.session() as session:
            for email, name, role in DEMO_USERS:
                if await session.scalar(select(User.id).where(User.email == email)) is None:
                    session.add(User(email=email, full_name=name, role=Role(role),
                                     hashed_password=hash_password(DEMO_PASSWORD)))  # fmt: skip
            if await session.scalar(select(Project.id).where(Project.key == "LDG")) is None:
                session.add(Project(key="LDG", **LDG_CONFIG))
            project = await session.scalar(select(Project).where(Project.key == "PHX"))
            if project is None:
                session.add(Project(key="PHX", **PHX_CONFIG))
            else:
                # Older demo databases: add link keys introduced since (e.g. slack_channels)
                # without overwriting anything edited by hand.
                project.links = {**PHX_CONFIG["links"], **(project.links or {})}
            await session.commit()
    finally:
        await database.dispose()
    for line in await seed_knowledge("admin@example.com"):
        print(" ", line)


def main() -> None:
    seed_only = "--seed-only" in sys.argv  # used by the Docker entrypoint
    (ROOT / "data").mkdir(exist_ok=True)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, check=True)
    asyncio.run(seed())
    print(f"\nDemo users (password: {DEMO_PASSWORD}):")
    for email, _, role in DEMO_USERS:
        print(f"  {role:<16} {email}")
    print()
    if seed_only:
        return

    import uvicorn

    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
