"""CRM: which customer commitments (open deals) depend on which project.

Deals carry the project's Jira key in a field you choose (Salesforce Opportunity field,
default ``Project_Key__c``; HubSpot deal property, default ``project_key``). Field names
and keys are validated before they reach SOQL or a search filter.
"""

import re
from typing import Any

from backend.app.core.config import Settings
from backend.app.integrations.connectors.auth import OAuthToken
from backend.app.integrations.connectors.base import (
    ConnectorClient,
    ConnectorService,
    Deal,
    to_date,
)
from backend.app.integrations.connectors.spec import ConnectorSpec

PROJECT_KEY = re.compile(r"^[A-Z][A-Z0-9_]{1,31}$")
FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,79}$")


def project_key(value: str) -> str:
    value = value.strip().upper()
    if not PROJECT_KEY.fullmatch(value):
        raise ValueError(f"Invalid project key: {value!r}")
    return value


def field_name(value: str) -> str:
    if not FIELD.fullmatch(value):
        raise ValueError(f"Invalid CRM field name: {value!r}")
    return value


def _float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


class SalesforceService(ConnectorService):
    source = "salesforce"

    def __init__(
        self, client_factory: Any, project_field: str, api_version: str, instance_url: str
    ) -> None:
        super().__init__(client_factory)
        self.project_field = field_name(project_field)
        if not re.fullmatch(r"v\d{2,3}\.\d", api_version):
            raise ValueError(f"Invalid Salesforce API version {api_version!r}")
        self.api_version = api_version
        self.instance_url = instance_url.rstrip("/")

    async def list_deals(self, project: str | None = None, open_only: bool = True,
                         limit: int = 50) -> list[Deal]:  # fmt: skip
        f = self.project_field
        where = ["IsClosed = false"] if open_only else []
        where.append(f"{f} = '{project_key(project)}'" if project else f"{f} != null")
        soql = (
            f"SELECT Id, Name, StageName, Amount, CloseDate, {f} FROM Opportunity "  # noqa: S608 - validated
            f"WHERE {' AND '.join(where)} ORDER BY CloseDate LIMIT {int(limit)}"
        )
        body = await self.client.get(f"/services/data/{self.api_version}/query", {"q": soql})
        return [Deal(source=self.source, id=r.get("Id", ""), name=r.get("Name", ""),
                     stage=r.get("StageName"), amount=_float(r.get("Amount")),
                     close_date=to_date(r.get("CloseDate")), project_key=r.get(f),
                     url=f"{self.instance_url}/lightning/r/Opportunity/{r.get('Id')}/view")
                for r in body.get("records") or []]  # fmt: skip

    async def health_check(self) -> dict[str, Any]:
        body = await self.client.get(f"/services/data/{self.api_version}/limits")
        return {"api_requests_remaining": (body.get("DailyApiRequests") or {}).get("Remaining")}


class HubSpotService(ConnectorService):
    source = "hubspot"

    def __init__(self, client_factory: Any, project_property: str) -> None:
        super().__init__(client_factory)
        self.prop = field_name(project_property)

    async def list_deals(self, project: str | None = None, open_only: bool = True,
                         limit: int = 50) -> list[Deal]:  # fmt: skip
        filters = [{"propertyName": self.prop, "operator": "EQ", "value": project_key(project)}
                   if project else {"propertyName": self.prop, "operator": "HAS_PROPERTY"}]  # fmt: skip
        if open_only:
            filters.append({"propertyName": "hs_is_closed", "operator": "EQ", "value": "false"})
        body = await self.client.post("/crm/v3/objects/deals/search", {
            "filterGroups": [{"filters": filters}],
            "properties": ["dealname", "dealstage", "amount", "closedate", self.prop],
            "sorts": [{"propertyName": "closedate", "direction": "ASCENDING"}],
            "limit": min(limit, 100),
        })  # fmt: skip
        return [Deal(source=self.source, id=str(r.get("id", "")),
                     name=(r.get("properties") or {}).get("dealname") or "",
                     stage=(r.get("properties") or {}).get("dealstage"),
                     amount=_float((r.get("properties") or {}).get("amount")),
                     close_date=to_date((r.get("properties") or {}).get("closedate")),
                     project_key=(r.get("properties") or {}).get(self.prop))
                for r in body.get("results") or []]  # fmt: skip

    async def health_check(self) -> dict[str, Any]:
        body = await self.client.get("/account-info/v3/details")
        return {"portal": body.get("portalId")}


class DemoCRM:
    def __init__(self, source: str, data: dict[str, Any]) -> None:
        self.source = source
        self.deals = [Deal(source=source, **d) for d in data.get("deals", [])]

    async def list_deals(self, project: str | None = None, open_only: bool = True,
                         limit: int = 50) -> list[Deal]:  # fmt: skip
        rows = [
            d
            for d in self.deals
            if not project or (d.project_key or "").upper() == project_key(project)
        ]
        return [
            d
            for d in rows
            if not open_only or (d.stage or "").lower() not in {"closed won", "closed lost"}
        ][:limit]

    async def health_check(self) -> dict[str, str]:
        return {"mode": "demo"}

    async def close(self) -> None:
        return None


def _salesforce(s: Settings) -> SalesforceService:
    token = OAuthToken("Salesforce", f"{s.salesforce_url.rstrip('/')}/services/oauth2/token",
                       s.salesforce_client_id, s.salesforce_client_secret.get_secret_value(),
                       timeout=s.connector_timeout_seconds)  # fmt: skip
    return SalesforceService(
        lambda: ConnectorClient("Salesforce", base_url=s.salesforce_url, timeout=s.connector_timeout_seconds,
                                token_provider=token),
        s.salesforce_project_field, s.salesforce_api_version, s.salesforce_url,
    )  # fmt: skip


CRM_SPECS = (
    ConnectorSpec(
        "salesforce", "Salesforce", "crm",
        required=("salesforce_url", "salesforce_client_id", "salesforce_client_secret"),
        optional=("salesforce_api_version", "salesforce_project_field"),
        setup="External Client App / Connected App with the OAuth client-credentials flow and a "
              "read-only run-as user; add an Opportunity field holding the Jira project key",
        docs_url="https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/",
        build=_salesforce,
        demo=lambda data, today: DemoCRM("salesforce", data.get("crm", {}).get("salesforce", {})),
    ),
    ConnectorSpec(
        "hubspot", "HubSpot", "crm", required=("hubspot_token",), optional=("hubspot_project_property",),
        setup="Settings → Integrations → Private apps (scope crm.objects.deals.read); add a deal "
              "property holding the Jira project key",
        docs_url="https://developers.hubspot.com/docs/api/crm/deals",
        build=lambda s: HubSpotService(
            lambda: ConnectorClient("HubSpot", base_url="https://api.hubapi.com", timeout=s.connector_timeout_seconds,
                                    headers={"Authorization": f"Bearer {s.hubspot_token.get_secret_value()}"}),
            s.hubspot_project_property),
        demo=lambda data, today: DemoCRM("hubspot", data.get("crm", {}).get("hubspot", {})),
    ),
)  # fmt: skip
