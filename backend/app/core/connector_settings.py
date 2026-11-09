"""Settings for the connectors beyond the core four (see docs/integrations.md).

A mixin of ``Settings`` so every value still comes from the environment / ``.env`` with
the same names (``LINEAR_API_KEY``, ``PAGERDUTY_API_KEY``, ...). A connector turns on when
its required fields are set (``integrations/connectors/registry.py``).
"""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings


class ConnectorSettings(BaseSettings):
    connector_timeout_seconds: float = Field(default=30.0, gt=0)

    # ---------------------------------------------------------------- issue trackers
    linear_api_key: SecretStr = SecretStr("")
    linear_api_url: str = "https://api.linear.app/graphql"
    linear_team_keys: list[str] = Field(default_factory=list)  # empty = every team
    gitlab_token: SecretStr = SecretStr("")
    gitlab_url: str = "https://gitlab.com"
    gitlab_projects: list[str] = Field(default_factory=list)  # "group/project"; empty = any
    azure_devops_org_url: str = ""  # https://dev.azure.com/<organization>
    azure_devops_pat: SecretStr = SecretStr("")
    azure_devops_projects: list[str] = Field(default_factory=list)
    asana_token: SecretStr = SecretStr("")
    asana_projects: list[str] = Field(default_factory=list)  # project gids
    clickup_token: SecretStr = SecretStr("")
    clickup_lists: list[str] = Field(default_factory=list)  # list ids
    bitbucket_workspace: str = ""
    bitbucket_username: str = ""  # Atlassian account email
    bitbucket_api_token: SecretStr = SecretStr("")
    bitbucket_repos: list[str] = Field(default_factory=list)

    # ---------------------------------------------------------------- engineering ops
    pagerduty_api_key: SecretStr = SecretStr("")
    pagerduty_services: list[str] = Field(default_factory=list)  # service ids; empty = all
    opsgenie_api_key: SecretStr = SecretStr("")
    opsgenie_api_url: str = "https://api.opsgenie.com"  # EU: https://api.eu.opsgenie.com
    sentry_token: SecretStr = SecretStr("")
    sentry_org: str = ""
    sentry_url: str = "https://sentry.io"
    jenkins_url: str = ""
    jenkins_username: str = ""
    jenkins_api_token: SecretStr = SecretStr("")
    jenkins_jobs: list[str] = Field(default_factory=list)
    argocd_url: str = ""
    argocd_token: SecretStr = SecretStr("")
    sonarqube_url: str = "https://sonarcloud.io"
    sonarqube_token: SecretStr = SecretStr("")

    # ---------------------------------------------------------------- HR
    bamboohr_company: str = ""  # the subdomain in https://<company>.bamboohr.com
    bamboohr_api_key: SecretStr = SecretStr("")
    # Zoho (People and Books share one OAuth client + refresh token; region-specific URLs).
    zoho_accounts_url: str = "https://accounts.zoho.com"
    zoho_client_id: str = ""
    zoho_client_secret: SecretStr = SecretStr("")
    zoho_refresh_token: SecretStr = SecretStr("")
    zoho_people_url: str = "https://people.zoho.com"
    zoho_books_url: str = "https://www.zohoapis.com/books/v3"
    zoho_books_organization_id: str = ""
    darwinbox_url: str = ""  # https://<tenant>.darwinbox.in
    darwinbox_username: str = ""
    darwinbox_password: SecretStr = SecretStr("")
    darwinbox_api_key: SecretStr = SecretStr("")
    workday_leave_report_url: str = ""  # a RaaS custom report returning time off
    workday_username: str = ""  # integration system user
    workday_password: SecretStr = SecretStr("")

    # ---------------------------------------------------------------- finance
    tally_url: str = ""  # Tally Prime HTTP server, e.g. http://localhost:9000
    tally_company: str = ""
    sap_odata_url: str = ""  # OData entity set exposing project budget vs actuals
    sap_username: str = ""
    sap_password: SecretStr = SecretStr("")
    sap_project_field: str = "Project"
    sap_budget_field: str = "Budget"
    sap_actual_field: str = "Actual"
    sap_currency_field: str = "Currency"

    # ---------------------------------------------------------------- CRM
    salesforce_url: str = ""  # https://<domain>.my.salesforce.com
    salesforce_client_id: str = ""
    salesforce_client_secret: SecretStr = SecretStr("")
    salesforce_api_version: str = "v61.0"
    salesforce_project_field: str = "Project_Key__c"  # Opportunity field holding the key
    hubspot_token: SecretStr = SecretStr("")
    hubspot_project_property: str = "project_key"  # deal property holding the key

    # ---------------------------------------------------------------- documents
    confluence_url: str = ""  # https://<site>.atlassian.net/wiki
    confluence_email: str = ""
    confluence_api_token: SecretStr = SecretStr("")
    confluence_spaces: list[str] = Field(default_factory=list)  # space keys to sync
    notion_token: SecretStr = SecretStr("")
    notion_version: str = "2022-06-28"
    # Microsoft Graph app (SharePoint, Outlook calendar, Teams transcripts).
    ms_tenant_id: str = ""
    ms_client_id: str = ""
    ms_client_secret: SecretStr = SecretStr("")
    sharepoint_site_id: str = ""
    sharepoint_drive_id: str = ""  # optional; default document library otherwise
    outlook_mailbox: str = ""  # user whose calendar is read
    teams_organizer_user: str = ""  # user id/UPN whose meeting transcripts are read
    # Google service account (Drive, Calendar, Meet), optionally with domain-wide delegation.
    google_service_account_file: Path | None = None
    google_delegated_user: str = ""
    google_drive_folder_id: str = ""
    google_calendar_id: str = "primary"
    # Synced documents get this visibility unless the source marks them restricted.
    docs_default_visibility: Literal["organization", "roles"] = "organization"
    docs_default_roles: list[str] = Field(default_factory=list)

    # ---------------------------------------------------------------- meetings
    zoom_account_id: str = ""
    zoom_client_id: str = ""
    zoom_client_secret: SecretStr = SecretStr("")

    # ---------------------------------------------------------------- chat
    slack_bot_token: SecretStr = SecretStr("")
    slack_signing_secret: SecretStr = SecretStr("")
    slack_digest_channel: str = ""
    teams_outgoing_webhook_secret: SecretStr = SecretStr("")  # base64, from Teams
    teams_workflow_webhook_url: SecretStr = SecretStr("")  # where answers are posted
