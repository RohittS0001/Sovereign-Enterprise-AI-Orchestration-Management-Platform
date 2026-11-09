"""Database layer: URL handling, migrations in sync with models, admin bootstrap."""

import argparse
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from backend.app.core.security.passwords import verify_password
from backend.app.core.security.permissions import Role
from backend.app.db.models import User
from backend.app.db.session import Database, normalize_database_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://u:p@db:5432/app", "postgresql+psycopg://u:p@db:5432/app"),
        ("postgres://u:p@db/app", "postgresql+psycopg://u:p@db/app"),
        ("postgresql+psycopg://u:p@db/app", "postgresql+psycopg://u:p@db/app"),
        ("sqlite:///./data/app.db", "sqlite+aiosqlite:///./data/app.db"),
        ("sqlite+aiosqlite:///:memory:", "sqlite+aiosqlite:///:memory:"),
    ],
)
def test_normalize_database_url(url, expected) -> None:
    assert normalize_database_url(url) == expected


def _alembic_config(url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.cmd_opts = argparse.Namespace(x=[f"url={url}"])
    return config


def test_migrations_upgrade_downgrade_and_match_models(tmp_path) -> None:
    config = _alembic_config(f"sqlite:///{tmp_path / 'migrations.db'}")

    command.upgrade(config, "head")
    # Fails if a model changed without a matching migration.
    command.check(config)
    command.downgrade(config, "base")
    command.upgrade(config, "head")


async def test_create_admin_script_creates_then_promotes(tmp_path, monkeypatch) -> None:
    from scripts import create_admin as script

    url = f"sqlite+aiosqlite:///{tmp_path / 'admin.db'}"
    database = Database(url)
    await database.create_all()

    settings = type("S", (), {"database_url": url})()
    monkeypatch.setattr(script, "get_settings", lambda: settings)

    assert await script.create_admin("Root@Example.com", "Root", "Str0ng!Passw0rd") == "created"
    assert await script.create_admin("root@example.com", "Root", "N3w!Passw0rd!!") == "updated"

    async with database.session() as session:
        users = list(await session.scalars(select(User)))
    await database.dispose()

    assert len(users) == 1
    assert users[0].email == "root@example.com"
    assert users[0].role == Role.ADMIN
    assert verify_password("N3w!Passw0rd!!", users[0].hashed_password)


async def test_datetimes_come_back_timezone_aware(database, make_user) -> None:
    from datetime import UTC

    user = await make_user()

    async with database.session() as session:
        fetched = await session.get(User, user.id)

    assert fetched.created_at.tzinfo is not None
    assert fetched.created_at.utcoffset() == UTC.utcoffset(None)
