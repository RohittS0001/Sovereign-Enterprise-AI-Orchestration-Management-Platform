import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from backend.app.core.config import Settings
from backend.app.core.security.passwords import hash_password
from backend.app.core.security.permissions import Role
from backend.app.core.security.tokens import create_access_token
from backend.app.db.models import User
from backend.app.db.session import Database
from backend.app.main import create_app

TEST_PASSWORD = "Str0ng!Passw0rd"  # noqa: S105 (test-only credential)
TEST_JWT_SECRET = "test-jwt-secret-key-that-is-long-enough-0123456789"  # noqa: S105


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Run tests marked 'live' that call real external services.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-live") or os.getenv("RUN_LIVE_TESTS") == "1":
        return

    skip_live = pytest.mark.skip(reason="live test: pass --run-live to enable")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture(autouse=True)
def _no_real_gmail_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The repository root holds the developer's real Gmail token; Settings built anywhere in a
    test (even with _env_file=None) must never find it, so the paths point at tmp_path."""
    monkeypatch.setenv("GMAIL_CREDENTIALS_FILE", str(tmp_path / "no-credentials.json"))
    monkeypatch.setenv("GMAIL_TOKEN_FILE", str(tmp_path / "no-token.json"))


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    """Settings isolated from the developer's .env and real credentials."""
    return Settings(
        _env_file=None,
        environment="test",
        log_level="WARNING",
        log_format="json",
        jira_base_url="",
        jira_email="",
        jira_api_token="",
        gmail_client_id="",
        gmail_client_secret="",
        gmail_refresh_token="",
        gmail_credentials_file=tmp_path / "credentials.json",
        gmail_token_file=tmp_path / "token.json",
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret_key=TEST_JWT_SECRET,
    )


@pytest.fixture
async def database(test_settings: Settings) -> AsyncIterator[Database]:
    db = Database(test_settings.database_url)
    await db.create_all()
    yield db
    await db.dispose()


@asynccontextmanager
async def serve(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Run the app's lifespan and yield an HTTP client bound to it (same event loop)."""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture
def app(test_settings: Settings, database: Database) -> FastAPI:
    return create_app(test_settings, database)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with serve(app) as test_client:
        yield test_client


MakeUser = Callable[..., Awaitable[User]]


@pytest.fixture
def make_user(database: Database) -> MakeUser:
    async def factory(
        *,
        email: str | None = None,
        role: Role = Role.MEMBER,
        password: str = TEST_PASSWORD,
        is_active: bool = True,
        full_name: str = "Test User",
    ) -> User:
        user = User(
            email=email or f"user-{uuid.uuid4().hex[:8]}@example.com",
            full_name=full_name,
            hashed_password=hash_password(password),
            role=role,
            is_active=is_active,
        )
        async with database.session() as session:
            session.add(user)
            await session.commit()
        return user

    return factory


@pytest.fixture
def auth_headers(test_settings: Settings) -> Callable[[User], dict[str, str]]:
    def build(user: User) -> dict[str, str]:
        token, _ = create_access_token(user.id, user.role, test_settings)
        return {"Authorization": f"Bearer {token}"}

    return build
