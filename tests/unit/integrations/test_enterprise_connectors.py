"""Datadog, ServiceNow, Google Cloud and Zendesk against mocked APIs."""

import pytest

from backend.app.integrations.connectors.cloud import GcpCloudService, gcp_label
from backend.app.integrations.connectors.ops import (
    DatadogService,
    ServiceNowService,
    servicenow_value,
)
from backend.app.integrations.connectors.support import ZendeskService, zendesk_tag
from tests.support.connectors import MockApi


async def test_datadog_incidents_and_alerting_monitors_by_service() -> None:
    api = MockApi(
        {
            "GET /api/v2/incidents": {
                "data": [
                    {
                        "id": "a",
                        "attributes": {
                            "public_id": 312,
                            "title": "Ledger lag",
                            "created": "2026-11-08T10:00:00Z",
                            "fields": {
                                "services": {"value": ["ledger-api"]},
                                "severity": {"value": "SEV-2"},
                                "state": {"value": "active"},
                            },
                        },
                    },
                    {
                        "id": "b",
                        "attributes": {
                            "public_id": 313,
                            "title": "Other team",
                            "fields": {
                                "services": {"value": ["web"]},
                                "state": {"value": "active"},
                            },
                        },
                    },
                    {
                        "id": "c",
                        "attributes": {
                            "public_id": 300,
                            "title": "Old",
                            "fields": {
                                "services": {"value": ["ledger-api"]},
                                "state": {"value": "resolved"},
                            },
                        },
                    },
                ]
            },
            "GET /api/v1/monitor": [
                {
                    "id": 88,
                    "name": "error rate > 2%",
                    "overall_state": "Alert",
                    "tags": ["service:ledger-api"],
                    "overall_state_modified": "2026-11-09T01:00:00Z",
                }
            ],
        }
    )
    svc = DatadogService(api.factory("Datadog", "https://api.datadoghq.com"))

    incidents = await svc.list_incidents(scope=["ledger-api"])

    assert [i.id for i in incidents] == ["312", "monitor-88"]
    assert incidents[0].severity == "SEV-2" and incidents[1].status == "triggered"
    assert api.last.url.params["monitor_tags"] == "service:ledger-api"


async def test_servicenow_incidents_and_changes_with_a_safe_query() -> None:
    api = MockApi(
        {
            "GET /api/now/table/incident": {
                "result": [
                    {
                        "number": "INC0012045",
                        "short_description": "Month-end close failing",
                        "state": "In Progress",
                        "priority": "2 - High",
                        "cmdb_ci": {"display_value": "Ledger API"},
                        "opened_at": "2026-11-07 09:00:00",
                    }
                ]
            },
            "GET /api/now/table/change_request": {
                "result": [
                    {
                        "number": "CHG1",
                        "short_description": "Batch v2.3",
                        "close_code": "Successful",
                        "closed_at": "2026-11-05 18:00:00",
                        "cmdb_ci": {"display_value": "Ledger API"},
                    },
                    {
                        "number": "CHG2",
                        "short_description": "Rollback",
                        "close_code": "Unsuccessful",
                        "closed_at": "2026-10-29 18:00:00",
                        "cmdb_ci": {"display_value": "Ledger API"},
                    },
                ]
            },
        }
    )
    svc = ServiceNowService(api.factory("ServiceNow", "https://acme.service-now.com"))

    [incident] = await svc.list_incidents(scope=["Ledger API"])
    query = api.last.url.params["sysparm_query"]
    changes = await svc.list_deployments(scope=["Ledger API"])

    assert incident.severity == "high" and incident.service == "Ledger API"
    assert query.startswith("active=true^cmdb_ci.nameINLedger API")
    assert [c.status for c in changes] == ["success", "failure"]
    for hostile in ("Ledger^ORactive=false", "a,b", "x=y"):
        with pytest.raises(ValueError):
            servicenow_value(hostile)


async def test_gcp_assets_alerts_and_security_findings() -> None:
    vm = "//compute.googleapis.com/projects/acme/zones/asia-south1-a/instances/ledger-1"
    api = MockApi(
        {
            "GET /v1/projects/acme:searchAllResources": {
                "results": [
                    {
                        "name": vm,
                        "assetType": "compute.googleapis.com/Instance",
                        "displayName": "ledger-1",
                        "location": "asia-south1-a",
                        "state": "RUNNING",
                        "createTime": "2026-06-01T00:00:00Z",
                        "labels": {"project": "ldg", "purpose": "ledger api"},
                        "additionalAttributes": {
                            "machineType": "zones/asia-south1-a/machineTypes/e2-standard-4"
                        },
                    }
                ]
            },
            "GET /v3/projects/acme/alerts": {"alerts": []},
            "GET /v1/projects/acme/sources/-/findings": {
                "listFindingsResults": [
                    {
                        "finding": {
                            "name": "f1",
                            "category": "OPEN_FIREWALL",
                            "severity": "HIGH",
                            "state": "ACTIVE",
                            "resourceName": vm,
                            "eventTime": "2026-11-07T00:00:00Z",
                        }
                    },
                    {
                        "finding": {
                            "name": "f2",
                            "category": "OTHER",
                            "severity": "LOW",
                            "state": "ACTIVE",
                            "resourceName": "//other",
                        }
                    },
                ]
            },
        }
    )
    svc = GcpCloudService(
        api.factory("Google Cloud", "https://cloudasset.googleapis.com"), ["acme"], "project"
    )

    [resource] = await svc.resources(["LDG"])
    findings = await svc.findings(["LDG"], {resource.id})

    assert api.requests[0].url.params["query"] == "labels.project=ldg"
    assert (resource.kind, resource.size, resource.health, resource.purpose) == (
        "instance",
        "e2-standard-4",
        "ok",
        "ledger api",
    )
    assert [f.title for f in findings] == ["Open firewall"] and findings[0].severity == "high"
    assert await svc.costs(["LDG"], None) is None  # needs a BigQuery billing export
    with pytest.raises(ValueError):
        gcp_label("LDG OR labels.x=y")


async def test_zendesk_tickets_by_project_tag() -> None:
    api = MockApi(
        {
            "GET /api/v2/search.json": {
                "results": [
                    {
                        "id": 48211,
                        "subject": "Refund not received",
                        "status": "open",
                        "priority": "high",
                        "created_at": "2026-11-06T00:00:00Z",
                        "updated_at": "2026-11-08T00:00:00Z",
                        "tags": ["phx"],
                    }
                ]
            }
        }
    )
    svc = ZendeskService(api.factory("Zendesk", "https://acme.zendesk.com"), "acme")

    [ticket] = await svc.list_tickets(["PHX"])

    assert api.last.url.params["query"] == "type:ticket tags:phx status<solved"
    assert (
        ticket.priority == "high" and ticket.url == "https://acme.zendesk.com/agent/tickets/48211"
    )
    with pytest.raises(ValueError):
        zendesk_tag("phx status:solved")
