"""Live checks against a real Jira site.

Skipped by default. Run with ``pytest -m live --run-live`` once JIRA_* settings point
at a real site.
"""

import pytest

from backend.app.core.config.settings import settings
from backend.app.integrations.jira.client import JiraClient

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not settings.jira_configured, reason="Jira is not configured"),
]


@pytest.mark.asyncio
async def test_jira_connection() -> None:
    async with JiraClient() as client:
        user = await client.get_current_user()

    assert user.get("accountId")
    assert user.get("displayName")
