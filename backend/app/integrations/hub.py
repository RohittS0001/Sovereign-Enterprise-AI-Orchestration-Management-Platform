"""One place that knows which integrations are active and how to check them."""

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

from backend.app.core.config import Settings
from backend.app.core.logging import get_logger
from backend.app.integrations.demo.services import build_demo_services

logger = get_logger(__name__)

IntegrationName = Literal["jira", "github", "keka", "gmail"]
INTEGRATIONS: tuple[IntegrationName, ...] = ("jira", "github", "keka", "gmail")


@dataclass
class HealthResult:
    name: str
    healthy: bool
    detail: str


@dataclass
class IntegrationHub:
    mode: Literal["live", "demo"]
    jira: Any = None
    github: Any = None
    keka: Any = None
    email: Any = None

    def service(self, name: IntegrationName) -> Any:
        return self.email if name == "gmail" else getattr(self, name)

    def enabled(self) -> dict[str, bool]:
        return {name: self.service(name) is not None for name in INTEGRATIONS}

    async def check(self, name: IntegrationName, limit_seconds: float = 10.0) -> HealthResult:
        service = self.service(name)
        if service is None:
            return HealthResult(name, False, "not configured")
        if self.mode == "demo":
            return HealthResult(name, True, "demo data")
        try:
            async with asyncio.timeout(limit_seconds):
                if name == "jira":
                    await service.client.get_current_user()
                elif name == "gmail":
                    await asyncio.to_thread(service.get_profile)
                else:
                    await service.health_check()
            return HealthResult(name, True, "ok")
        except TimeoutError:
            return HealthResult(name, False, f"timed out after {limit_seconds:g}s")
        except Exception as exc:  # report, never raise from a health check
            message = getattr(exc, "message", None) or exc.__class__.__name__
            return HealthResult(name, False, str(message)[:300])

    async def close(self) -> None:
        for service in (self.jira, self.github, self.keka):
            close = getattr(service, "close", None)
            if close is not None:
                await close()


def build_integration_hub(settings: Settings) -> IntegrationHub:
    """Real services for configured integrations, or demo services in DEMO_MODE.

    Services build their HTTP clients lazily, so this never touches the network.
    """
    if settings.demo_mode:
        demo = build_demo_services()
        return IntegrationHub(
            mode="demo",
            jira=demo["jira"],
            github=demo["github"],
            keka=demo["keka"],
            email=demo["email"],
        )

    from backend.app.integrations.github.client import GitHubClient
    from backend.app.integrations.github.service import GitHubService
    from backend.app.integrations.gmail.client import GmailClient
    from backend.app.integrations.gmail.service import EmailService
    from backend.app.integrations.jira.client import JiraClient
    from backend.app.integrations.jira.service import JiraService
    from backend.app.integrations.keka.client import KekaClient
    from backend.app.integrations.keka.service import KekaService

    s = settings  # clients are built from *these* settings, not the global ones

    def jira() -> JiraClient:
        return JiraClient(
            s.jira_base_url,
            s.jira_email,
            s.jira_api_token.get_secret_value(),
            s.jira_timeout_seconds,
        )

    def github() -> GitHubClient:
        return GitHubClient(
            s.github_token.get_secret_value(), s.github_api_url, s.github_timeout_seconds
        )

    def keka() -> KekaClient:
        return KekaClient(
            s.keka_base_url,
            s.keka_token_url,
            s.keka_client_id,
            s.keka_client_secret.get_secret_value(),
            s.keka_api_key.get_secret_value(),
            s.keka_timeout_seconds,
        )

    return IntegrationHub(
        mode="live",
        jira=JiraService(client_factory=jira) if s.jira_configured else None,
        github=GitHubService(allowed_repos=s.github_allowed_repos, client_factory=github)
        if s.github_configured else None,
        keka=KekaService(client_factory=keka) if s.keka_configured else None,
        email=EmailService(client_factory=lambda: GmailClient(settings=s))
        if s.gmail_configured else None,
    )  # fmt: skip
