"""What every integration declares: its settings (env vars), how to build it, how to fake it.

The registry of specs drives the connection status API, ``python -m scripts.connections``,
``docs/integrations.md`` and the test that keeps ``.env.example`` complete — so connecting a
real system is always "fill in the listed variables and restart".
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import SecretStr

from backend.app.core.config import Settings

Category = Literal[
    "core",
    "tracker",
    "ops",
    "cloud",
    "hr",
    "finance",
    "crm",
    "docs",
    "calendar",
    "meetings",
    "chat",
]

CATEGORY_TITLES: dict[str, str] = {
    "core": "Core (Jira, GitHub, Keka, Gmail)",
    "tracker": "Issue trackers & code hosting",
    "ops": "Engineering operations",
    "cloud": "Cloud (resources, cost, health, security)",
    "support": "Customer support",
    "hr": "HR systems",
    "finance": "Finance & accounting",
    "crm": "CRM",
    "docs": "Document sources (knowledge base sync)",
    "calendar": "Calendars",
    "meetings": "Meeting transcripts",
    "chat": "Chat (Slack / Teams)",
}


def env_name(field_name: str) -> str:
    return field_name.upper()


def is_set(value: Any) -> bool:
    if isinstance(value, SecretStr):
        return bool(value.get_secret_value())
    if isinstance(value, Path):
        return value.exists()
    return bool(value)


@dataclass(frozen=True)
class ConnectorSpec:
    name: str
    title: str
    category: Category
    required: tuple[str, ...]  # Settings field names; all must be set to enable it
    optional: tuple[str, ...] = ()
    setup: str = ""  # where the credentials come from
    docs_url: str = ""
    # Live service from settings (None for the core four, which the hub builds itself).
    build: Callable[[Settings], Any] | None = None
    # Demo twin from the demo dataset and "today" (None = no demo twin).
    demo: Callable[[dict[str, Any], date], Any] | None = None
    # Overrides "all required fields set" (e.g. Gmail: env credentials OR a token file).
    configured_check: Callable[[Settings], bool] | None = field(default=None, compare=False)

    @property
    def env_vars(self) -> tuple[str, ...]:
        return tuple(env_name(f) for f in (*self.required, *self.optional))

    def missing(self, settings: Settings) -> list[str]:
        if self.configured(settings):
            return []
        return [env_name(f) for f in self.required if not is_set(getattr(settings, f))]

    def configured(self, settings: Settings) -> bool:
        if self.configured_check is not None:
            return self.configured_check(settings)
        return all(is_set(getattr(settings, f)) for f in self.required)
