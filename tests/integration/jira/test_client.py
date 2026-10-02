import pytest

from backend.app.integrations.jira.client import JiraClient


@pytest.mark.asyncio
async def test_jira_connection() -> None:
    client = JiraClient()

    try:
        user = await client.get_current_user()

        assert user.get("accountId")
        assert user.get("displayName")

        print("\nJira connection successful")
        print("Display name:", user.get("displayName"))
        print("Account ID:", user.get("accountId"))

    finally:
        await client.close()