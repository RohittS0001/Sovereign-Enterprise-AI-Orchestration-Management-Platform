"""CRM connectors against mocked APIs, and the customer-commitments risk factor."""

from datetime import date, timedelta

import httpx
import pytest

from backend.app.domains.analytics.results import SprintForecast
from backend.app.domains.analytics.risk import commitment_factor
from backend.app.domains.analytics.snapshot import ProjectSnapshot
from backend.app.integrations.connectors.auth import OAuthToken
from backend.app.integrations.connectors.base import ConnectorClient, Deal
from backend.app.integrations.connectors.crm import HubSpotService, SalesforceService
from tests.support.connectors import MockApi

TODAY = date(2026, 11, 9)


async def test_salesforce_uses_client_credentials_and_validated_soql() -> None:
    api = MockApi({
        "POST /services/oauth2/token": {"access_token": "sf-token", "expires_in": 3600},
        "GET /services/data/v61.0/query": {"records": [
            {"Id": "006A", "Name": "Northwind", "StageName": "Negotiation", "Amount": 1000,
             "CloseDate": "2026-11-20", "Project_Key__c": "PHX"}]},
    })  # fmt: skip
    transport = httpx.MockTransport(api)
    token = OAuthToken("Salesforce", "https://acme.my.salesforce.com/services/oauth2/token",
                       "cid", "secret", transport=transport)  # fmt: skip
    svc = SalesforceService(
        lambda: ConnectorClient("Salesforce", base_url="https://acme.my.salesforce.com", timeout=5,
                                token_provider=token, transport=transport),
        "Project_Key__c", "v61.0", "https://acme.my.salesforce.com",
    )  # fmt: skip

    [deal] = await svc.list_deals(project="phx")

    form = api.requests[0].content.decode()
    assert "grant_type=client_credentials" in form and "client_id=cid" in form
    soql = api.last.url.params["q"]
    assert "WHERE IsClosed = false AND Project_Key__c = 'PHX'" in soql
    assert api.last.headers["authorization"] == "Bearer sf-token"
    assert (deal.project_key, deal.amount) == ("PHX", 1000.0)
    assert deal.url == "https://acme.my.salesforce.com/lightning/r/Opportunity/006A/view"
    with pytest.raises(ValueError):
        await svc.list_deals(project="PHX' OR Name != '")
    with pytest.raises(ValueError):
        SalesforceService(lambda: None, "Key__c FROM User--", "v61.0", "https://x")


async def test_hubspot_searches_by_project_property() -> None:
    api = MockApi({"POST /crm/v3/objects/deals/search": {"results": [
        {"id": "1", "properties": {"dealname": "Contoso", "dealstage": "contractsent",
                                   "amount": "45000", "closedate": "2026-12-01T00:00:00Z",
                                   "project_key": "PHX"}}]}})  # fmt: skip
    svc = HubSpotService(api.factory("HubSpot", "https://api.hubapi.com"), "project_key")

    [deal] = await svc.list_deals(project="PHX")

    filters = api.json_body()["filterGroups"][0]["filters"]
    assert filters[0] == {"propertyName": "project_key", "operator": "EQ", "value": "PHX"}
    assert {"propertyName": "hs_is_closed", "operator": "EQ", "value": "false"} in filters
    assert (deal.name, deal.amount, deal.close_date) == ("Contoso", 45000.0, date(2026, 12, 1))


def _forecast(status: str) -> SprintForecast:
    return SprintForecast(sprint="S", start=TODAY, end=TODAY, working_days_total=10,
                          working_days_elapsed=5, working_days_left=5, committed_points=10,
                          done_points=2, burn_rate_per_day=0.4, projected_points=4,
                          completion_ratio=0.4, status=status, remaining_issues=[])  # fmt: skip


def test_commitments_count_only_when_delivery_slips() -> None:
    deals = [Deal(source="hubspot", id="1", name="Soon", stage="contract", amount=10,
                  close_date=TODAY + timedelta(days=10)),
             Deal(source="hubspot", id="2", name="Later", close_date=TODAY + timedelta(days=90))]  # fmt: skip
    snap = ProjectSnapshot(key="PHX", name="P", today=TODAY, deals=deals)

    slipping = commitment_factor(snap, _forecast("off_track"))
    fine = commitment_factor(snap, _forecast("on_track"))

    assert slipping.score == 3 and "Soon" in slipping.evidence[0] and len(slipping.evidence) == 1
    assert fine.score == 0 and "not at risk yet" in fine.evidence[-1]
