"""AWS and Azure cloud monitoring: signing, inventory, health, storage, cost, alarms, findings."""

import json
from datetime import UTC, date, datetime
from urllib.parse import parse_qs

import httpx
import pytest

from backend.app.core.config import Settings
from backend.app.db.models import Project
from backend.app.domains.cloud.service import portfolio, project_cloud
from backend.app.integrations.connectors.auth import aws_sigv4_headers
from backend.app.integrations.connectors.cloud import (
    AwsClient,
    AwsCloudService,
    AzureCloudService,
    month_bounds,
    tag_value,
)
from backend.app.integrations.hub import build_integration_hub
from tests.support.analytics import PHX_CONFIG
from tests.support.connectors import MockApi

TODAY = date(2026, 11, 9)


def test_sigv4_matches_the_aws_test_suite_vector() -> None:
    # "get-vanilla" from the AWS Signature Version 4 test suite.
    headers = aws_sigv4_headers(
        "GET",
        "https://example.amazonaws.com/",
        region="us-east-1",
        service="service",
        access_key="AKIDEXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        now=datetime(2015, 8, 30, 12, 36, tzinfo=UTC),
    )
    assert headers["authorization"].endswith(
        "Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"
    )
    assert headers["x-amz-date"] == "20150830T123600Z"


def test_tag_values_are_validated_and_month_bounds() -> None:
    assert tag_value("PHX") == "PHX"
    for bad in ("x' or 1=1", "a\nb", ""):
        with pytest.raises(ValueError):
            tag_value(bad)
    assert month_bounds(date(2026, 11, 9)) == (
        date(2026, 11, 1),
        date(2026, 11, 9),
        date(2026, 11, 30),
    )


EC2_INSTANCES = """<DescribeInstancesResponse xmlns="http://ec2.amazonaws.com/doc/2016-11-15/">
<reservationSet><item><instancesSet><item>
  <instanceId>i-1</instanceId><instanceType>m6i.large</instanceType>
  <instanceState><name>running</name></instanceState><launchTime>2026-09-29T10:00:00.000Z</launchTime>
  <placement><availabilityZone>ap-south-1a</availabilityZone></placement>
</item></instancesSet></item></reservationSet></DescribeInstancesResponse>"""
EC2_STATUS = """<DescribeInstanceStatusResponse xmlns="http://ec2.amazonaws.com/doc/2016-11-15/">
<instanceStatusSet><item><instanceId>i-1</instanceId>
  <instanceStatus><status>impaired</status></instanceStatus><systemStatus><status>ok</status></systemStatus>
</item></instanceStatusSet></DescribeInstanceStatusResponse>"""
EBS = """<DescribeVolumesResponse xmlns="http://ec2.amazonaws.com/doc/2016-11-15/"><volumeSet><item>
  <volumeId>vol-1</volumeId><size>100</size><volumeType>gp3</volumeType><status>in-use</status>
</item></volumeSet></DescribeVolumesResponse>"""
RDS = """<DescribeDBInstancesResponse xmlns="http://rds.amazonaws.com/doc/2014-10-31/"><DescribeDBInstancesResult>
<DBInstances><DBInstance><DBInstanceArn>arn:aws:rds:ap-south-1:1:db:pay</DBInstanceArn>
  <DBInstanceClass>db.r6g.large</DBInstanceClass><Engine>postgres</Engine><DBInstanceStatus>available</DBInstanceStatus>
  <AllocatedStorage>200</AllocatedStorage></DBInstance></DBInstances></DescribeDBInstancesResult></DescribeDBInstancesResponse>"""
S3_METRIC = """<GetMetricStatisticsResponse xmlns="http://monitoring.amazonaws.com/doc/2010-08-01/">
<GetMetricStatisticsResult><Datapoints><member><Timestamp>2026-11-08T00:00:00Z</Timestamp>
<Average>5368709120</Average></member></Datapoints></GetMetricStatisticsResult></GetMetricStatisticsResponse>"""
ALARMS = """<DescribeAlarmsResponse xmlns="http://monitoring.amazonaws.com/doc/2010-08-01/"><DescribeAlarmsResult>
<MetricAlarms><member><AlarmName>pay-p99</AlarmName><AlarmArn>arn:aws:cloudwatch:ap-south-1:1:alarm:pay-p99</AlarmArn>
<StateValue>ALARM</StateValue><StateReason>p99 above 800 ms</StateReason>
<StateUpdatedTimestamp>2026-11-09T05:00:00Z</StateUpdatedTimestamp></member></MetricAlarms>
</DescribeAlarmsResult></DescribeAlarmsResponse>"""

TAGGED = {
    "ResourceTagMappingList": [
        {
            "ResourceARN": "arn:aws:ec2:ap-south-1:1:instance/i-1",
            "Tags": [
                {"Key": "project", "Value": "PHX"},
                {"Key": "Name", "Value": "api-1"},
                {"Key": "purpose", "Value": "Payments API"},
                {"Key": "environment", "Value": "production"},
            ],
        },
        {
            "ResourceARN": "arn:aws:ec2:ap-south-1:1:volume/vol-1",
            "Tags": [{"Key": "project", "Value": "PHX"}],
        },
        {
            "ResourceARN": "arn:aws:rds:ap-south-1:1:db:pay",
            "Tags": [{"Key": "project", "Value": "PHX"}],
        },
        {"ResourceARN": "arn:aws:s3:::phx-receipts", "Tags": [{"Key": "project", "Value": "PHX"}]},
        {
            "ResourceARN": "arn:aws:cloudwatch:ap-south-1:1:alarm:pay-p99",
            "Tags": [{"Key": "project", "Value": "PHX"}],
        },
    ]
}


def aws_handler(seen: list[httpx.Request]):
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIA/")
        host = request.url.host
        if host.startswith("tagging."):
            assert json.loads(request.content)["TagFilters"] == [
                {"Key": "project", "Values": ["PHX"]}
            ]
            return httpx.Response(200, json=TAGGED)
        if host.startswith("ce."):
            target = request.headers["x-amz-target"]
            if target.endswith("GetCostAndUsage"):
                return httpx.Response(
                    200,
                    json={
                        "ResultsByTime": [
                            {
                                "Groups": [
                                    {
                                        "Keys": ["Amazon EC2"],
                                        "Metrics": {
                                            "UnblendedCost": {"Amount": "612.4", "Unit": "USD"}
                                        },
                                    },
                                    {
                                        "Keys": ["Amazon RDS"],
                                        "Metrics": {
                                            "UnblendedCost": {"Amount": "401.25", "Unit": "USD"}
                                        },
                                    },
                                ]
                            }
                        ]
                    },
                )
            return httpx.Response(200, json={"Total": {"Amount": "1500", "Unit": "USD"}})
        if host.startswith("securityhub."):
            return httpx.Response(
                200,
                json={
                    "Findings": [
                        {
                            "Id": "f1",
                            "Title": "Bucket is public",
                            "Severity": {"Label": "CRITICAL"},
                            "Workflow": {"Status": "NEW"},
                            "Resources": [{"Id": "arn:aws:s3:::phx-receipts"}],
                            "CreatedAt": "2026-11-06T00:00:00Z",
                            "Remediation": {"Recommendation": {"Text": "Block public access"}},
                        }
                    ]
                },
            )
        action = parse_qs(request.content.decode())["Action"][0]
        body = {
            "DescribeInstances": EC2_INSTANCES,
            "DescribeInstanceStatus": EC2_STATUS,
            "DescribeVolumes": EBS,
            "DescribeDBInstances": RDS,
            "GetMetricStatistics": S3_METRIC,
            "DescribeAlarms": ALARMS,
        }[action]
        return httpx.Response(200, text=body)

    return handle


async def test_aws_inventory_health_storage_cost_alarms_and_findings() -> None:
    seen: list[httpx.Request] = []
    transport = httpx.MockTransport(aws_handler(seen))
    svc = AwsCloudService(
        lambda: AwsClient("AKIA", "secret", transport=transport),
        ["ap-south-1"],
        "project",
        cost_explorer=True,
    )

    resources = {r.kind: r for r in await svc.resources(["PHX"])}
    cost = await svc.costs(["PHX"], TODAY)
    again = await svc.costs(["PHX"], TODAY)
    alerts = await svc.alerts(["PHX"])
    findings = await svc.findings(["PHX"])

    instance = resources["instance"]
    assert (instance.name, instance.size, instance.state, instance.region) == (
        "api-1",
        "m6i.large",
        "running",
        "ap-south-1a",
    )
    assert instance.health == "impaired" and instance.purpose == "Payments API"
    assert instance.environment == "production" and instance.created == date(2026, 9, 29)
    assert resources["disk"].storage_gb == 100 and resources["database"].storage_gb == 200
    assert resources["bucket"].storage_gb == 5.0
    assert "alarm" not in {r.service for r in resources.values()}
    assert cost.total == 1013.65 and cost.forecast_month_end == 2513.65
    assert [line.service for line in cost.by_service] == ["Amazon EC2", "Amazon RDS"]
    assert again is cost  # cached: Cost Explorer is billed per request
    assert sum(r.url.host.startswith("ce.") for r in seen) == 2
    assert alerts[0].title == "pay-p99: p99 above 800 ms" and alerts[0].severity == "high"
    assert (
        findings[0].severity == "critical" and findings[0].recommendation == "Block public access"
    )


async def test_aws_cost_explorer_is_off_by_default() -> None:
    seen: list[httpx.Request] = []
    svc = AwsCloudService(
        lambda: AwsClient("AKIA", "s", transport=httpx.MockTransport(aws_handler(seen))),
        ["ap-south-1"],
        "project",
    )
    assert await svc.costs(["PHX"], TODAY) is None and not seen


async def test_azure_resource_graph_cost_and_alerts() -> None:
    vm = "/subscriptions/sub1/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/web-1"

    def graph(request: httpx.Request):
        query = json.loads(request.content)["query"]
        if query.startswith("Resources"):
            assert "in~ ('PHX')" in query
            return {
                "data": [
                    {
                        "id": vm,
                        "name": "web-1",
                        "type": "Microsoft.Compute/virtualMachines",
                        "location": "centralindia",
                        "tags": {"project": "PHX", "purpose": "Web tier"},
                        "size": "Standard_D2s_v5",
                        "state": "VM running",
                        "diskGB": None,
                        "dbGB": None,
                        "created": "2026-10-01T00:00:00Z",
                    }
                ]
            }
        if query.startswith("HealthResources"):
            return {"data": [{"targetId": vm.lower(), "status": "Degraded", "summary": "High CPU"}]}
        return {
            "data": [
                {
                    "id": "a1",
                    "title": "Enable disk encryption",
                    "severity": "High",
                    "resourceId": vm.lower(),
                    "remediation": "Turn on ADE",
                    "created": None,
                }
            ]
        }

    api = MockApi(
        {
            "POST /providers/Microsoft.ResourceGraph/resources": graph,
            "POST /subscriptions/sub1/providers/Microsoft.CostManagement/query": {
                "properties": {
                    "columns": [{"name": "Cost"}, {"name": "ServiceName"}, {"name": "Currency"}],
                    "rows": [[96.2, "Azure Container Apps", "USD"], [8.4, "Storage", "USD"]],
                }
            },
            "POST /subscriptions/sub1/providers/Microsoft.CostManagement/forecast": {
                "properties": {"columns": [{"name": "Cost"}], "rows": [[10.0], [12.5]]}
            },
            "GET /subscriptions/sub1/providers/Microsoft.AlertsManagement/alerts": {
                "value": [
                    {
                        "id": "al1",
                        "name": "High CPU on web-1",
                        "properties": {
                            "essentials": {
                                "severity": "Sev1",
                                "targetResource": vm,
                                "monitorCondition": "Fired",
                                "startDateTime": "2026-11-09T03:00:00Z",
                            }
                        },
                    },
                    {
                        "id": "al2",
                        "name": "Other project",
                        "properties": {
                            "essentials": {
                                "severity": "Sev0",
                                "targetResource": "/subscriptions/sub1/x",
                                "monitorCondition": "Fired",
                            }
                        },
                    },
                ]
            },
        }
    )
    svc = AzureCloudService(api.factory("Azure", "https://management.azure.com"), "sub1", "project")

    [resource] = await svc.resources(["PHX"])
    cost = await svc.costs(["PHX"], TODAY)
    alerts = await svc.alerts(["PHX"], {resource.id})
    findings = await svc.findings(["PHX"], {resource.id})

    assert resource.kind == "instance" and resource.health == "warning"
    assert resource.health_detail == "High CPU" and resource.purpose == "Web tier"
    assert cost.total == 104.6 and cost.forecast_month_end == 127.1
    assert [a.title for a in alerts] == ["High CPU on web-1"] and alerts[0].severity == "high"
    assert findings[0].severity == "high" and findings[0].recommendation == "Turn on ADE"


async def test_project_summary_portfolio_and_security_visibility() -> None:
    hub = build_integration_hub(Settings(_env_file=None, demo_mode=True), demo_today=TODAY)
    phx = Project(key="PHX", **PHX_CONFIG, is_active=True)
    ldg = Project(
        key="LDG",
        name="Ledger Modernisation",
        members=[{"name": "Kiran P"}],
        links={},
        github_repos=[],
        is_active=True,
    )

    admin_view = await project_cloud(hub, phx, TODAY, include_security=True)
    lead_view = await project_cloud(hub, phx, TODAY, include_security=False)
    overview = await portfolio(hub, [phx, ldg], TODAY, include_security=True)

    assert admin_view.clouds == ["aws", "azure", "gcp"] and len(admin_view.resources) == 11
    assert admin_view.states["impaired"] == 1 and admin_view.states["warning"] == 1
    assert admin_view.storage_gb == pytest.approx(731.0)
    assert admin_view.cost.month_to_date == pytest.approx(1252.15)
    assert admin_view.cost.by_cloud == {"aws": 1126.55, "azure": 125.6}
    assert admin_view.findings[0].severity == "critical"  # most severe first
    assert lead_view.findings is None and lead_view.findings_by_severity["critical"] == 1
    assert {p.region for p in admin_view.placements} >= {"ap-south-1", "centralindia"}
    rows = {r.project: r for r in overview.projects}
    assert rows["LDG"].resources == 4 and rows["LDG"].critical_findings == 0
    assert overview.people["Kiran P"] == ["LDG"] and "PHX" in overview.people["Arjun M"]
    assert overview.totals["projects"] == 2
