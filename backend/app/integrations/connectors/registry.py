"""Every integration the platform knows about, in one list."""

from backend.app.core.config import Settings
from backend.app.integrations.connectors.ops import OPS_SPECS
from backend.app.integrations.connectors.spec import ConnectorSpec
from backend.app.integrations.connectors.trackers import TRACKER_SPECS

CORE_SPECS: tuple[ConnectorSpec, ...] = (
    ConnectorSpec(
        "jira", "Jira Cloud", "core",
        required=("jira_base_url", "jira_email", "jira_api_token"),
        optional=("jira_timeout_seconds", "jira_story_points_field"),
        setup="API token from id.atlassian.com → Security → API tokens; base URL "
              "https://<site>.atlassian.net",
        docs_url="https://developer.atlassian.com/cloud/jira/platform/rest/v3/",
    ),
    ConnectorSpec(
        "github", "GitHub", "core",
        required=("github_token",),
        optional=("github_api_url", "github_allowed_repos", "github_timeout_seconds"),
        setup="Fine-grained token: read PRs, contents, actions, checks (+ PR write for comments)",
        docs_url="https://docs.github.com/rest",
    ),
    ConnectorSpec(
        "keka", "Keka HRMS", "core",
        required=("keka_base_url", "keka_client_id", "keka_client_secret", "keka_api_key"),
        optional=("keka_token_url", "keka_timeout_seconds"),
        setup="Keka admin → Integrations → API access (client id/secret + API key)",
        docs_url="https://developers.keka.com/",
    ),
    ConnectorSpec(
        "gmail", "Gmail (drafts)", "core",
        required=("gmail_client_id", "gmail_client_secret", "gmail_refresh_token"),
        optional=("gmail_redirect_uri", "gmail_credentials_file", "gmail_token_file",
                  "gmail_allowed_recipient_domains"),
        setup="Google Cloud OAuth client; servers set the refresh token, local dev runs "
              "`python -m scripts.gmail_auth` once to create GMAIL_TOKEN_FILE",
        docs_url="https://developers.google.com/gmail/api",
        configured_check=lambda s: s.gmail_configured,
    ),
)  # fmt: skip

# Connectors beyond the core four, grouped by category.
CONNECTOR_SPECS: tuple[ConnectorSpec, ...] = (*TRACKER_SPECS, *OPS_SPECS)

ALL_SPECS: tuple[ConnectorSpec, ...] = CORE_SPECS + CONNECTOR_SPECS


def spec(name: str) -> ConnectorSpec:
    for item in ALL_SPECS:
        if item.name == name:
            return item
    raise KeyError(name)


def configured_connectors(settings: Settings) -> list[ConnectorSpec]:
    return [s for s in CONNECTOR_SPECS if s.configured(settings)]
