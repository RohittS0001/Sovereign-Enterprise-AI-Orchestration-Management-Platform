"""One place that knows which integrations are active and how to check them."""

import asyncio
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from backend.app.core.config import Settings
from backend.app.core.logging import get_logger
from backend.app.integrations.demo.services import build_demo_services, load_connector_dataset

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
    # Connectors beyond the core four, by spec name (see integrations/connectors/).
    connectors: dict[str, Any] = field(default_factory=dict)

    def service(self, name: str) -> Any:
        if name in INTEGRATIONS:
            return self.email if name == "gmail" else getattr(self, name)
        return self.connectors.get(name)

    def enabled(self) -> dict[str, bool]:
        core = {name: self.service(name) is not None for name in INTEGRATIONS}
        return core | {name: True for name in self.connectors}

    def by_category(self, category: str) -> dict[str, Any]:
        """Active connector services of one category, e.g. every configured issue tracker."""
        from backend.app.integrations.connectors.registry import spec

        return {n: s for n, s in self.connectors.items() if spec(n).category == category}

    async def check(self, name: str, limit_seconds: float = 10.0) -> HealthResult:
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
        for service in (self.jira, self.github, self.keka, *self.connectors.values()):
            close = getattr(service, "close", None)
            if close is not None:
                await close()


def build_integration_hub(settings: Settings, *, demo_today: date | None = None) -> IntegrationHub:
    """Real services for configured integrations, or demo services in DEMO_MODE.

    Services build their HTTP clients lazily, so this never touches the network.
    ``demo_today`` anchors the demo dataset's relative dates (tests and evaluations pin it
    so results do not drift with the calendar).
    """
    from backend.app.integrations.connectors.registry import CONNECTOR_SPECS

    if settings.demo_mode:
        today = demo_today or date.today()
        demo = build_demo_services(today)
        data = load_connector_dataset(today)
        return IntegrationHub(
            mode="demo",
            jira=demo["jira"],
            github=demo["github"],
            keka=demo["keka"],
            email=demo["email"],
            connectors={s.name: s.demo(data, today) for s in CONNECTOR_SPECS if s.demo},
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
        connectors={c.name: c.build(s) for c in CONNECTOR_SPECS if c.build and c.configured(s)},
    )  # fmt: skip
