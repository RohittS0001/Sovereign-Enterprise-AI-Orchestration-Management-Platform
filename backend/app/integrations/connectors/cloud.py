"""Cloud monitoring (read-only): what runs for a project, where and why, its health, storage,
cost, alarms and security findings, on AWS and Azure.

A resource belongs to a project through a tag (``CLOUD_PROJECT_TAG_KEY``, default ``project``)
whose value is the project key or one of the project's ``cloud_tag_values``. ``purpose`` /
``description``, ``environment`` and ``owner`` tags explain why it exists and who runs it.

AWS (SigV4-signed REST, no SDK): the Resource Groups Tagging API finds every tagged resource in
one call per region; EC2, EBS and RDS add state, size, storage and status checks; CloudWatch
adds S3 bucket sizes and alarm states; Cost Explorer (opt-in: USD 0.01 per request, cached 12 h)
the month's cost by service and the forecast; Security Hub the active findings.

Azure: Resource Graph (one KQL query each for resources, Resource Health and Defender for Cloud
assessments), Cost Management (cost by service and forecast), Azure Monitor alerts and storage
account capacity metrics.
"""

import asyncio
import json
import re
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from xml.etree import ElementTree  # noqa: S405 - parses AWS responses only (see _xml)

import httpx

from backend.app.core.config import Settings
from backend.app.core.errors import IntegrationError
from backend.app.integrations.common.http import buffered, read_limited
from backend.app.integrations.connectors.auth import aws_sigv4_headers, azure_management_token
from backend.app.integrations.connectors.base import (
    CloudAlert,
    CloudResource,
    ConnectorClient,
    ConnectorService,
    CostLine,
    CostReport,
    SecurityFinding,
    to_date,
)
from backend.app.integrations.connectors.spec import ConnectorSpec

TAG_VALUE = re.compile(r"^[A-Za-z0-9._:/@+ -]{1,128}$")
COST_CACHE_SECONDS = 12 * 3600
MAX_PAGES = 5
SEVERITIES = ("critical", "high", "medium", "low", "informational")


def tag_value(value: str) -> str:
    """Tag keys/values go into API filters and KQL strings: allow a conservative set only."""
    if not TAG_VALUE.fullmatch(value) or "'" in value:
        raise ValueError(f"Invalid cloud tag value: {value!r}")
    return value


def _meta(tags: dict[str, str]) -> dict[str, str | None]:
    lower = {k.lower(): v for k, v in tags.items()}
    return {
        "environment": lower.get("environment") or lower.get("env") or lower.get("stage"),
        "purpose": lower.get("purpose") or lower.get("description") or lower.get("role"),
        "owner": lower.get("owner") or lower.get("team") or lower.get("created-by"),
    }


def month_bounds(today: date) -> tuple[date, date, date]:
    """(first day of the month, today, last day of the month)."""
    start = today.replace(day=1)
    next_month = (start + timedelta(days=32)).replace(day=1)
    return start, today, next_month - timedelta(days=1)


# ------------------------------------------------------------------ AWS


AWS_MAX_RESPONSE_BYTES = 10_000_000


class AwsClient:
    """Signs every request with SigV4; JSON, query (XML) and REST-JSON protocols."""

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        session_token: str = "",
        timeout: float = 20,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.access_key, self.secret_key, self.session_token = access_key, secret_key, session_token
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def _send(
        self, method: str, url: str, service: str, region: str, body: bytes, headers: dict[str, str]
    ) -> httpx.Response:
        signed = aws_sigv4_headers(
            method,
            url,
            region=region,
            service=service,
            access_key=self.access_key,
            secret_key=self.secret_key,
            body=body,
            headers=headers,
            session_token=self.session_token,
        )
        request = self._client.build_request(method, url, content=body, headers=signed)
        try:
            streamed = await self._client.send(request, stream=True)
            try:
                data = await read_limited(streamed, AWS_MAX_RESPONSE_BYTES, integration="aws")
            finally:
                await streamed.aclose()
        except httpx.TimeoutException as exc:
            raise IntegrationError(f"AWS {service} timed out", integration="aws") from exc
        except httpx.HTTPError as exc:
            raise IntegrationError(
                f"AWS {service} unreachable: {exc.__class__.__name__}", integration="aws"
            ) from exc
        response = buffered(streamed, data)
        if response.status_code >= 400:
            raise IntegrationError(
                f"AWS {service} error {response.status_code}: {response.text[:200]}",
                integration="aws",
                upstream_status=response.status_code,
            )
        return response

    async def json(
        self, service: str, region: str, target: str, payload: dict, host: str | None = None
    ) -> dict:
        url = f"https://{host or f'{service}.{region}.amazonaws.com'}/"
        body = json.dumps(payload).encode()
        response = await self._send(
            "POST",
            url,
            service,
            region,
            body,
            {"content-type": "application/x-amz-json-1.1", "x-amz-target": target},
        )
        return response.json()

    async def query(
        self,
        service: str,
        region: str,
        action: str,
        version: str,
        params: dict[str, Any] | None = None,
        host: str | None = None,
    ) -> ElementTree.Element:
        url = f"https://{host or f'{service}.{region}.amazonaws.com'}/"
        body = urlencode({"Action": action, "Version": version, **(params or {})}).encode()
        response = await self._send(
            "POST",
            url,
            service,
            region,
            body,
            {"content-type": "application/x-www-form-urlencoded; charset=utf-8"},
        )
        return _xml(response.text)

    async def rest(self, service: str, region: str, path: str, payload: dict) -> dict:
        url = f"https://{service}.{region}.amazonaws.com{path}"
        body = json.dumps(payload).encode()
        response = await self._send(
            "POST", url, service, region, body, {"content-type": "application/json"}
        )
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()


def _xml(text: str) -> ElementTree.Element:
    """Parse an AWS query-protocol response with namespaces stripped.

    Python 3.13's expat limits entity amplification and ElementTree never fetches external
    entities; the input is an AWS API response over TLS, capped in size here.
    """
    root = ElementTree.fromstring(text[:5_000_000])  # noqa: S314 - see docstring
    for element in root.iter():
        if "}" in element.tag:
            element.tag = element.tag.split("}", 1)[1]
    return root


def _text(element: ElementTree.Element | None, path: str) -> str | None:
    found = element.find(path) if element is not None else None
    return found.text if found is not None and found.text else None


def _arn_parts(arn: str) -> tuple[str, str, str]:
    """(service, region, resource) from an ARN."""
    parts = arn.split(":", 5)
    return (parts[2], parts[3], parts[5]) if len(parts) == 6 else ("", "", arn)


_AWS_KINDS = {
    ("ec2", "instance"): "instance",
    ("ec2", "volume"): "disk",
    ("rds", "db"): "database",
    ("s3", ""): "bucket",
    ("lambda", "function"): "function",
    ("ecs", "service"): "container",
    ("ecs", "cluster"): "container",
    ("eks", "cluster"): "container",
    ("elasticloadbalancing", "loadbalancer"): "network",
    ("dynamodb", "table"): "database",
    ("elasticfilesystem", "file-system"): "disk",
}


class AwsCloudService(ConnectorService):
    source = "aws"

    def __init__(
        self, client_factory: Any, regions: list[str], tag_key: str, cost_explorer: bool = False
    ) -> None:
        super().__init__(client_factory)
        self.regions = regions or ["us-east-1"]
        self.tag_key = tag_value(tag_key)
        self.cost_explorer = cost_explorer
        self._cost_cache: dict[tuple, tuple[float, CostReport | None]] = {}

    # ---------------------------------------------------------------- inventory

    async def _tagged(self, region: str, values: list[str]) -> list[dict]:
        found, token = [], ""
        for _ in range(MAX_PAGES):
            payload: dict[str, Any] = {
                "TagFilters": [{"Key": self.tag_key, "Values": values}],
                "ResourcesPerPage": 100,
            }
            if token:
                payload["PaginationToken"] = token
            body = await self.client.json(
                "tagging", region, "ResourceGroupsTaggingAPI_20170126.GetResources", payload
            )
            found += body.get("ResourceTagMappingList") or []
            token = body.get("PaginationToken") or ""
            if not token:
                break
        return found

    async def resources(self, values: list[str]) -> list[CloudResource]:
        values = [tag_value(v) for v in values]
        results: list[CloudResource] = []
        for region in self.regions:
            tagged = await self._tagged(region, values)
            by_kind: dict[str, list[CloudResource]] = {}
            for item in tagged:
                arn = item["ResourceARN"]
                service, arn_region, resource = _arn_parts(arn)
                rtype, _, rid = resource.replace(":", "/", 1).partition("/")
                if service == "s3":
                    rtype, rid = "", resource
                if service == "cloudwatch":
                    continue  # alarms are reported as alerts
                tags = {t["Key"]: t["Value"] for t in item.get("Tags") or []}
                kind = _AWS_KINDS.get((service, rtype), "other")
                results.append(
                    CloudResource(
                        source="aws",
                        id=arn,
                        name=tags.get("Name") or rid or resource,
                        kind=kind,
                        service=f"{service}:{rtype}" if rtype else service,
                        region=arn_region or region,
                        tags=tags,
                        **_meta(tags),
                    )
                )
                by_kind.setdefault(results[-1].service, []).append(results[-1])
            await asyncio.gather(
                self._ec2_instances(region, by_kind.get("ec2:instance", [])),
                self._ebs(region, by_kind.get("ec2:volume", [])),
                self._rds(region, by_kind.get("rds:db", [])),
                self._s3(region, by_kind.get("s3", [])),
            )
        return results

    async def _ec2_instances(self, region: str, items: list[CloudResource]) -> None:
        if not items:
            return
        ids = {r.id.rsplit("/", 1)[-1]: r for r in items}
        params = {f"InstanceId.{n}": i for n, i in enumerate(ids, start=1)}
        root = await self.client.query("ec2", region, "DescribeInstances", "2016-11-15", params)
        for instance in root.iter("instancesSet"):
            for item in instance.findall("item"):
                resource = ids.get(_text(item, "instanceId") or "")
                if resource is None:
                    continue
                resource.size = _text(item, "instanceType")
                resource.state = _text(item, "instanceState/name")
                resource.created = to_date(_text(item, "launchTime"))
                resource.region = _text(item, "placement/availabilityZone") or resource.region
                if resource.state in {"stopped", "stopping", "terminated"}:
                    resource.health = "stopped"
        status = await self.client.query(
            "ec2",
            region,
            "DescribeInstanceStatus",
            "2016-11-15",
            {**params, "IncludeAllInstances": "true"},
        )
        for item in status.iter("item"):
            resource = ids.get(_text(item, "instanceId") or "")
            if resource is None or resource.health == "stopped":
                continue
            checks = (_text(item, "instanceStatus/status"), _text(item, "systemStatus/status"))
            if "impaired" in checks:
                resource.health, resource.health_detail = "impaired", "EC2 status check failing"
            elif all(c == "ok" for c in checks):
                resource.health, resource.health_detail = "ok", "2/2 status checks passed"
            elif "initializing" in checks:
                resource.health, resource.health_detail = "warning", "Status checks initializing"

    async def _ebs(self, region: str, items: list[CloudResource]) -> None:
        if not items:
            return
        ids = {r.id.rsplit("/", 1)[-1]: r for r in items}
        params = {f"VolumeId.{n}": i for n, i in enumerate(ids, start=1)}
        root = await self.client.query("ec2", region, "DescribeVolumes", "2016-11-15", params)
        for item in root.iter("item"):
            resource = ids.get(_text(item, "volumeId") or "")
            if resource is not None:
                resource.storage_gb = float(_text(item, "size") or 0)
                resource.size, resource.state = _text(item, "volumeType"), _text(item, "status")
                resource.created = to_date(_text(item, "createTime"))
                resource.health = "ok" if resource.state in {"in-use", "available"} else "warning"

    async def _rds(self, region: str, items: list[CloudResource]) -> None:
        if not items:
            return
        by_arn = {r.id: r for r in items}
        root = await self.client.query("rds", region, "DescribeDBInstances", "2014-10-31")
        for db in root.iter("DBInstance"):
            resource = by_arn.get(_text(db, "DBInstanceArn") or "")
            if resource is None:
                continue
            resource.size = f"{_text(db, 'DBInstanceClass')} {_text(db, 'Engine') or ''}".strip()
            resource.state = _text(db, "DBInstanceStatus")
            resource.storage_gb = float(_text(db, "AllocatedStorage") or 0)
            resource.created = to_date(_text(db, "InstanceCreateTime"))
            resource.health = {"available": "ok", "stopped": "stopped"}.get(
                resource.state or "", "warning"
            )

    async def _s3(self, region: str, items: list[CloudResource]) -> None:
        now = datetime.now(UTC)
        for resource in items:
            params = {
                "Namespace": "AWS/S3",
                "MetricName": "BucketSizeBytes",
                "Period": "86400",
                "StartTime": (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "EndTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "Statistics.member.1": "Average",
                "Dimensions.member.1.Name": "BucketName",
                "Dimensions.member.1.Value": resource.name,
                "Dimensions.member.2.Name": "StorageType",
                "Dimensions.member.2.Value": "StandardStorage",
            }
            root = await self.client.query(
                "monitoring", region, "GetMetricStatistics", "2010-08-01", params
            )
            points = sorted(root.iter("member"), key=lambda m: _text(m, "Timestamp") or "")
            if points and _text(points[-1], "Average"):
                resource.storage_gb = round(float(_text(points[-1], "Average") or 0) / 1024**3, 3)
            resource.health, resource.state = "ok", "available"

    # ---------------------------------------------------------------- cost

    async def costs(self, values: list[str], today: date) -> CostReport | None:
        """Month-to-date cost by service and the month-end forecast (Cost Explorer, opt-in)."""
        if not self.cost_explorer:
            return None
        key = (tuple(sorted(values)), today)
        cached = self._cost_cache.get(key)
        if cached and time.monotonic() - cached[0] < COST_CACHE_SECONDS:
            return cached[1]
        start, end, month_end = month_bounds(today)
        tag_filter = {"Tags": {"Key": self.tag_key, "Values": [tag_value(v) for v in values]}}
        body = await self.client.json(
            "ce",
            "us-east-1",
            "AWSInsightsIndexService.GetCostAndUsage",
            {
                "TimePeriod": {
                    "Start": start.isoformat(),
                    "End": (end + timedelta(days=1)).isoformat(),
                },
                "Granularity": "MONTHLY",
                "Metrics": ["UnblendedCost"],
                "Filter": tag_filter,
                "GroupBy": [{"Type": "DIMENSION", "Key": "SERVICE"}],
            },
            host="ce.us-east-1.amazonaws.com",
        )
        lines, currency = [], "USD"
        for period in body.get("ResultsByTime") or []:
            for group in period.get("Groups") or []:
                metric = (group.get("Metrics") or {}).get("UnblendedCost") or {}
                currency = metric.get("Unit") or currency
                lines.append(
                    CostLine(
                        service=group["Keys"][0], amount=round(float(metric.get("Amount") or 0), 2)
                    )
                )
        forecast = None
        if today < month_end:
            try:
                f = await self.client.json(
                    "ce",
                    "us-east-1",
                    "AWSInsightsIndexService.GetCostForecast",
                    {
                        "TimePeriod": {
                            "Start": today.isoformat(),
                            "End": (month_end + timedelta(days=1)).isoformat(),
                        },
                        "Metric": "UNBLENDED_COST",
                        "Granularity": "MONTHLY",
                        "Filter": tag_filter,
                    },
                    host="ce.us-east-1.amazonaws.com",
                )
                total = sum(line.amount for line in lines)
                forecast = round(total + float((f.get("Total") or {}).get("Amount") or 0), 2)
            except IntegrationError:
                forecast = None  # not enough history yet
        report = CostReport(
            source="aws",
            currency=currency,
            period_start=start,
            period_end=end,
            total=round(sum(line.amount for line in lines), 2),
            forecast_month_end=forecast,
            by_service=sorted(lines, key=lambda line: -line.amount),
        )
        self._cost_cache[key] = (time.monotonic(), report)
        return report

    # ---------------------------------------------------------------- alarms & security

    async def alerts(
        self, values: list[str], resource_ids: set[str] | None = None
    ) -> list[CloudAlert]:
        alerts: list[CloudAlert] = []
        if not values:  # no project scope = nothing in scope (never the whole account)
            return alerts
        for region in self.regions:
            alarm_arns = [
                i["ResourceARN"]
                for i in await self._tagged(region, values)
                if ":cloudwatch:" in i["ResourceARN"] and ":alarm:" in i["ResourceARN"]
            ]
            if not alarm_arns:
                continue
            params = {
                f"AlarmNames.member.{n}": arn.rsplit(":alarm:", 1)[-1]
                for n, arn in enumerate(alarm_arns[:100], start=1)
            }
            root = await self.client.query(
                "monitoring", region, "DescribeAlarms", "2010-08-01", params
            )
            for alarm in root.iter("member"):
                state = _text(alarm, "StateValue")
                if state not in {"ALARM", "INSUFFICIENT_DATA"} or not _text(alarm, "AlarmName"):
                    continue
                started = _text(alarm, "StateUpdatedTimestamp")
                alerts.append(
                    CloudAlert(
                        source="aws",
                        id=_text(alarm, "AlarmArn") or _text(alarm, "AlarmName") or "",
                        title=f"{_text(alarm, 'AlarmName')}: {_text(alarm, 'StateReason') or state}"[
                            :300
                        ],
                        severity="high" if state == "ALARM" else "low",
                        state=state,
                        resource=_text(alarm, "Dimensions/member/Value"),
                        started=datetime.fromisoformat(started.replace("Z", "+00:00"))
                        if started
                        else None,
                    )
                )
        return alerts

    async def findings(
        self, values: list[str], resource_ids: set[str] | None = None
    ) -> list[SecurityFinding]:
        found: list[SecurityFinding] = []
        if not values:  # an empty ResourceTags filter would match every finding
            return found
        for region in self.regions:
            body = await self.client.rest(
                "securityhub",
                region,
                "/findings",
                {
                    "Filters": {
                        "ResourceTags": [
                            {"Key": self.tag_key, "Value": tag_value(v), "Comparison": "EQUALS"}
                            for v in values
                        ],
                        "RecordState": [{"Value": "ACTIVE", "Comparison": "EQUALS"}],
                        "WorkflowStatus": [
                            {"Value": s, "Comparison": "EQUALS"} for s in ("NEW", "NOTIFIED")
                        ],
                    },
                    "MaxResults": 100,
                },
            )
            for f in body.get("Findings") or []:
                resource = (f.get("Resources") or [{}])[0]
                found.append(
                    SecurityFinding(
                        source="aws",
                        id=f.get("Id", ""),
                        title=f.get("Title", "")[:300],
                        severity=(
                            (f.get("Severity") or {}).get("Label") or "informational"
                        ).lower(),
                        status=(f.get("Workflow") or {}).get("Status") or "NEW",
                        resource=resource.get("Id"),
                        created=to_date(f.get("CreatedAt")),
                        recommendation=(
                            (f.get("Remediation") or {}).get("Recommendation") or {}
                        ).get("Text"),
                        url=f.get("SourceUrl"),
                    )
                )
        return found

    async def health_check(self) -> dict[str, Any]:
        await self._tagged(self.regions[0], ["__health_check__"])
        return {"regions": self.regions, "cost_explorer": self.cost_explorer}


# ------------------------------------------------------------------ Azure


_AZURE_KINDS = {
    "microsoft.compute/virtualmachines": "instance",
    "microsoft.compute/disks": "disk",
    "microsoft.storage/storageaccounts": "bucket",
    "microsoft.sql/servers/databases": "database",
    "microsoft.dbforpostgresql/flexibleservers": "database",
    "microsoft.web/sites": "container",
    "microsoft.app/containerapps": "container",
    "microsoft.containerservice/managedclusters": "container",
    "microsoft.network/loadbalancers": "network",
    "microsoft.network/applicationgateways": "network",
    "microsoft.documentdb/databaseaccounts": "database",
}
_AZURE_HEALTH = {"available": "ok", "degraded": "warning", "unavailable": "impaired"}
_AZURE_SEVERITY = {
    "sev0": "critical",
    "sev1": "high",
    "sev2": "medium",
    "sev3": "low",
    "sev4": "low",
}


class AzureCloudService(ConnectorService):
    source = "azure"
    base = "https://management.azure.com"

    def __init__(self, client_factory: Any, subscription: str, tag_key: str) -> None:
        super().__init__(client_factory)
        self.subscription = tag_value(subscription)
        self.tag_key = tag_value(tag_key)
        self._cost_cache: dict[tuple, tuple[float, CostReport | None]] = {}

    async def _graph(self, query: str) -> list[dict]:
        body = await self.client.post(
            "/providers/Microsoft.ResourceGraph/resources?api-version=2022-10-01",
            {"subscriptions": [self.subscription], "query": query, "options": {"$top": 1000}},
        )
        return list(body.get("data") or [])

    def _in(self, values: list[str]) -> str:
        return ", ".join(f"'{tag_value(v)}'" for v in values)

    async def resources(self, values: list[str]) -> list[CloudResource]:
        rows = await self._graph(
            f"Resources | where tostring(tags['{self.tag_key}']) in~ ({self._in(values)}) "
            "| extend size=coalesce(tostring(properties.hardwareProfile.vmSize), tostring(sku.name)), "
            "state=coalesce(tostring(properties.extended.instanceView.powerState.displayStatus), "
            "tostring(properties.state), tostring(properties.provisioningState)), "
            "diskGB=todouble(properties.diskSizeGB), dbGB=todouble(properties.storage.storageSizeGB), "
            "created=coalesce(tostring(properties.timeCreated), tostring(properties.creationTime)) "
            "| project id, name, type, location, tags, size, state, diskGB, dbGB, created"
        )
        health = {
            str(h.get("targetId") or "").lower(): h
            for h in await self._graph(
                "HealthResources | where type =~ 'microsoft.resourcehealth/availabilitystatuses' "
                "| extend targetId=tolower(tostring(properties.targetResourceId)), "
                "status=tostring(properties.availabilityState), summary=tostring(properties.summary) "
                "| project targetId, status, summary"
            )
        }
        results = []
        for row in rows:
            rtype = str(row.get("type") or "").lower()
            tags = {str(k): str(v) for k, v in (row.get("tags") or {}).items()}
            h = health.get(str(row.get("id") or "").lower(), {})
            state = row.get("state")
            status = _AZURE_HEALTH.get(str(h.get("status") or "").lower(), "unknown")
            if state and "deallocated" in str(state).lower():
                status = "stopped"
            results.append(
                CloudResource(
                    source="azure",
                    id=row["id"],
                    name=row.get("name") or row["id"],
                    kind=_AZURE_KINDS.get(rtype, "other"),
                    service=row.get("type") or "",
                    size=row.get("size"),
                    region=row.get("location"),
                    state=state,
                    health=status,
                    health_detail=h.get("summary"),
                    storage_gb=row.get("diskGB") or row.get("dbGB"),
                    created=to_date(row.get("created")),
                    tags=tags,
                    **_meta(tags),
                    url=f"https://portal.azure.com/#resource{row['id']}",
                )
            )
        for resource in results:
            if resource.kind == "bucket":
                resource.storage_gb = await self._used_capacity_gb(resource.id)
        return results

    async def _used_capacity_gb(self, resource_id: str) -> float | None:
        try:
            body = await self.client.get(
                f"{resource_id}/providers/Microsoft.Insights/metrics",
                {
                    "api-version": "2023-10-01",
                    "metricnames": "UsedCapacity",
                    "aggregation": "Average",
                    "interval": "PT1H",
                    "timespan": "PT3H",
                },
            )
        except IntegrationError:
            return None
        series = [
            p.get("average")
            for v in body.get("value") or []
            for t in v.get("timeseries") or []
            for p in t.get("data") or []
            if p.get("average") is not None
        ]
        return round(series[-1] / 1024**3, 3) if series else None

    async def costs(self, values: list[str], today: date) -> CostReport | None:
        key = (tuple(sorted(values)), today)
        cached = self._cost_cache.get(key)
        if cached and time.monotonic() - cached[0] < COST_CACHE_SECONDS:
            return cached[1]
        start, end, month_end = month_bounds(today)
        tag_filter = {
            "tags": {
                "name": self.tag_key,
                "operator": "In",
                "values": [tag_value(v) for v in values],
            }
        }
        path = f"/subscriptions/{self.subscription}/providers/Microsoft.CostManagement"
        body = await self.client.post(
            f"{path}/query?api-version=2023-11-01",
            {
                "type": "ActualCost",
                "timeframe": "Custom",
                "timePeriod": {"from": f"{start}T00:00:00Z", "to": f"{end}T23:59:59Z"},
                "dataset": {
                    "granularity": "None",
                    "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
                    "grouping": [{"type": "Dimension", "name": "ServiceName"}],
                    "filter": tag_filter,
                },
            },
        )
        columns = [c["name"] for c in (body.get("properties") or {}).get("columns") or []]
        lines, currency = [], "USD"
        for row in (body.get("properties") or {}).get("rows") or []:
            record = dict(zip(columns, row, strict=False))
            currency = record.get("Currency") or currency
            lines.append(
                CostLine(
                    service=str(record.get("ServiceName") or "Other"),
                    amount=round(float(record.get("Cost") or 0), 2),
                )
            )
        total = round(sum(line.amount for line in lines), 2)
        forecast = None
        if today < month_end:
            try:
                f = await self.client.post(
                    f"{path}/forecast?api-version=2023-11-01",
                    {
                        "type": "Usage",
                        "timeframe": "Custom",
                        "includeActualCost": False,
                        "timePeriod": {
                            "from": f"{today + timedelta(days=1)}T00:00:00Z",
                            "to": f"{month_end}T23:59:59Z",
                        },
                        "dataset": {
                            "granularity": "Daily",
                            "filter": tag_filter,
                            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
                        },
                    },
                )
                cols = [c["name"] for c in (f.get("properties") or {}).get("columns") or []]
                rest = sum(
                    float(dict(zip(cols, r, strict=False)).get("Cost") or 0)
                    for r in (f.get("properties") or {}).get("rows") or []
                )
                forecast = round(total + rest, 2)
            except IntegrationError:
                forecast = None
        report = CostReport(
            source="azure",
            currency=currency,
            period_start=start,
            period_end=end,
            total=total,
            forecast_month_end=forecast,
            by_service=sorted(lines, key=lambda line: -line.amount),
        )
        self._cost_cache[key] = (time.monotonic(), report)
        return report

    async def alerts(
        self, values: list[str], resource_ids: set[str] | None = None
    ) -> list[CloudAlert]:
        ids = {i.lower() for i in resource_ids or set()}
        if not ids:  # the project has no resources here: nothing is in scope
            return []
        body = await self.client.get(
            f"/subscriptions/{self.subscription}/providers/Microsoft.AlertsManagement/alerts",
            {"api-version": "2019-05-05-preview", "monitorCondition": "Fired", "alertState": "New"},
        )
        alerts = []
        for alert in body.get("value") or []:
            essentials = (alert.get("properties") or {}).get("essentials") or {}
            target = str(essentials.get("targetResource") or "")
            if target.lower() not in ids:
                continue
            started = essentials.get("startDateTime")
            alerts.append(
                CloudAlert(
                    source="azure",
                    id=alert.get("id", ""),
                    title=alert.get("name", "")[:300],
                    severity=_AZURE_SEVERITY.get(str(essentials.get("severity")).lower(), "medium"),
                    state=str(essentials.get("monitorCondition") or "Fired"),
                    resource=target or None,
                    started=datetime.fromisoformat(started.replace("Z", "+00:00"))
                    if started
                    else None,
                )
            )
        return alerts

    async def findings(
        self, values: list[str], resource_ids: set[str] | None = None
    ) -> list[SecurityFinding]:
        ids = {i.lower() for i in resource_ids or set()}
        if not ids:  # assessments are subscription-wide: no project resources, no findings
            return []
        rows = await self._graph(
            "SecurityResources | where type == 'microsoft.security/assessments' "
            "| extend status=tostring(properties.status.code), severity=tostring(properties.metadata.severity), "
            "title=tostring(properties.displayName), resourceId=tolower(tostring(properties.resourceDetails.Id)), "
            "remediation=tostring(properties.metadata.remediationDescription), "
            "created=tostring(properties.status.firstEvaluationDate) "
            "| where status == 'Unhealthy' | project id, title, severity, resourceId, remediation, created"
        )
        return [
            SecurityFinding(
                source="azure",
                id=r["id"],
                title=str(r.get("title") or "")[:300],
                severity=str(r.get("severity") or "informational").lower(),
                status="Unhealthy",
                resource=r.get("resourceId"),
                created=to_date(r.get("created")),
                recommendation=r.get("remediation"),
            )
            for r in rows
            if str(r.get("resourceId") or "").lower() in ids
        ]

    async def health_check(self) -> dict[str, Any]:
        await self._graph("Resources | take 1 | project id")
        return {"subscription": self.subscription}


# ------------------------------------------------------------------ Google Cloud


_GCP_KINDS = {
    "compute.googleapis.com/Instance": "instance",
    "compute.googleapis.com/Disk": "disk",
    "storage.googleapis.com/Bucket": "bucket",
    "sqladmin.googleapis.com/Instance": "database",
    "run.googleapis.com/Service": "container",
    "container.googleapis.com/Cluster": "container",
    "cloudfunctions.googleapis.com/Function": "function",
    "compute.googleapis.com/ForwardingRule": "network",
}
_GCP_HEALTH = {
    "RUNNING": "ok",
    "RUNNABLE": "ok",
    "READY": "ok",
    "ACTIVE": "ok",
    "TERMINATED": "stopped",
    "STOPPED": "stopped",
    "SUSPENDED": "stopped",
    "PROVISIONING": "warning",
    "STAGING": "warning",
    "REPAIRING": "impaired",
}
_GCP_LABEL = re.compile(r"^[a-z0-9_-]{1,63}$")


def gcp_label(value: str) -> str:
    """GCP label keys/values are lowercase letters, digits, - and _."""
    value = value.lower()
    if not _GCP_LABEL.fullmatch(value):
        raise ValueError(f"Invalid Google Cloud label: {value!r}")
    return value


class GcpCloudService(ConnectorService):
    """Cloud Asset Inventory (resources by label), Cloud Monitoring alerts, Security Command
    Center findings. Cost needs a BigQuery billing export, so it is not reported here."""

    source = "gcp"

    def __init__(self, client_factory: Any, projects: list[str], label_key: str) -> None:
        super().__init__(client_factory)
        self.projects = [tag_value(p) for p in projects]
        self.label_key = gcp_label(label_key)

    async def resources(self, values: list[str]) -> list[CloudResource]:
        query = " OR ".join(f"labels.{self.label_key}={gcp_label(v)}" for v in values)
        results = []
        for project in self.projects:
            body = await self.client.get(
                f"https://cloudasset.googleapis.com/v1/projects/{project}:searchAllResources",
                {"query": query, "pageSize": 500},
            )
            for r in body.get("results") or []:
                labels = {str(k): str(v) for k, v in (r.get("labels") or {}).items()}
                extra = r.get("additionalAttributes") or {}
                asset_type = r.get("assetType", "")
                state = r.get("state")
                size_gb = extra.get("sizeGb") or extra.get("diskSizeGb")
                results.append(
                    CloudResource(
                        source="gcp",
                        id=r.get("name", ""),
                        name=r.get("displayName") or r.get("name", ""),
                        kind=_GCP_KINDS.get(asset_type, "other"),
                        service=asset_type,
                        size=extra.get("machineType", "").rsplit("/", 1)[-1]
                        or extra.get("tier")
                        or None,
                        region=r.get("location"),
                        state=state,
                        health=_GCP_HEALTH.get(str(state).upper(), "unknown")
                        if state
                        else "unknown",
                        storage_gb=float(size_gb) if size_gb else None,
                        created=to_date(r.get("createTime")),
                        tags=labels,
                        **_meta(labels),
                        url=f"https://console.cloud.google.com/home/dashboard?project={project}",
                    )
                )
        return results

    async def costs(self, values: list[str], today: date) -> CostReport | None:
        return None  # Cloud Billing has no per-label cost API; it needs a BigQuery export

    async def alerts(
        self, values: list[str], resource_ids: set[str] | None = None
    ) -> list[CloudAlert]:
        alerts: list[CloudAlert] = []
        wanted = {v.lower() for v in values}
        if not wanted:
            return alerts
        for project in self.projects:
            try:
                body = await self.client.get(
                    f"https://monitoring.googleapis.com/v3/projects/{project}/alerts",
                    {"filter": 'state="OPEN"', "pageSize": 100},
                )
            except IntegrationError:
                continue  # the Alerts API is not enabled in every project
            for a in body.get("alerts") or []:
                labels = (a.get("resource") or {}).get("labels") or {}
                metadata = (a.get("metadata") or {}).get("userLabels") or {}
                if str(metadata.get(self.label_key, "")).lower() not in wanted:
                    continue
                opened = a.get("openTime")
                alerts.append(
                    CloudAlert(
                        source="gcp",
                        id=a.get("name", ""),
                        title=(a.get("policy") or {}).get("displayName") or a.get("name", ""),
                        severity=str((a.get("policy") or {}).get("severity") or "medium").lower(),
                        state=a.get("state", "OPEN"),
                        resource=labels.get("instance_id") or labels.get("project_id"),
                        started=datetime.fromisoformat(opened.replace("Z", "+00:00"))
                        if opened
                        else None,
                    )
                )
        return alerts

    async def findings(
        self, values: list[str], resource_ids: set[str] | None = None
    ) -> list[SecurityFinding]:
        ids = set(resource_ids or set())
        found: list[SecurityFinding] = []
        if not ids:  # findings are project-wide in GCP: keep only this project's resources
            return found
        for project in self.projects:
            body = await self.client.get(
                f"https://securitycenter.googleapis.com/v1/projects/{project}/sources/-/findings",
                {"filter": 'state="ACTIVE"', "pageSize": 200},
            )
            for item in body.get("listFindingsResults") or []:
                f = item.get("finding") or {}
                resource = f.get("resourceName")
                if resource not in ids:
                    continue
                found.append(
                    SecurityFinding(
                        source="gcp",
                        id=f.get("name", ""),
                        title=f.get("category", "").replace("_", " ").capitalize(),
                        severity=str(f.get("severity") or "informational").lower(),
                        status=f.get("state", "ACTIVE"),
                        resource=resource,
                        created=to_date(f.get("eventTime")),
                        recommendation=f.get("nextSteps"),
                        url=f.get("externalUri"),
                    )
                )
        return found

    async def health_check(self) -> dict[str, Any]:
        return {"projects": self.projects}


# ------------------------------------------------------------------ demo twin


class DemoCloud:
    """In-memory AWS/Azure with tagged resources, costs, alerts and findings."""

    def __init__(self, source: str, data: dict[str, Any], today: date) -> None:
        self.source = source
        self.today = today
        self.tag_key = "project"
        self.data = data

    def _project(self, item: dict, values: list[str]) -> bool:
        return str((item.get("tags") or {}).get(self.tag_key, "")).lower() in {
            v.lower() for v in values
        }

    async def resources(self, values: list[str]) -> list[CloudResource]:
        return [
            CloudResource(
                source=self.source, **{k: v for k, v in r.items()}, **_meta(r.get("tags") or {})
            )
            for r in self.data.get("resources", [])
            if self._project(r, values)
        ]

    async def costs(self, values: list[str], today: date) -> CostReport | None:
        start, end, _ = month_bounds(today)
        for project, cost in (self.data.get("costs") or {}).items():
            if project.lower() in {v.lower() for v in values}:
                lines = [CostLine(service=s, amount=a) for s, a in cost["by_service"].items()]
                return CostReport(
                    source=self.source,
                    currency=cost.get("currency", "USD"),
                    period_start=start,
                    period_end=end,
                    total=round(sum(line.amount for line in lines), 2),
                    forecast_month_end=cost.get("forecast"),
                    by_service=sorted(lines, key=lambda line: -line.amount),
                )
        return None

    async def alerts(
        self, values: list[str], resource_ids: set[str] | None = None
    ) -> list[CloudAlert]:
        return [
            CloudAlert(source=self.source, **{k: v for k, v in a.items() if k != "tags"})
            for a in self.data.get("alerts", [])
            if self._project(a, values)
        ]

    async def findings(
        self, values: list[str], resource_ids: set[str] | None = None
    ) -> list[SecurityFinding]:
        return [
            SecurityFinding(source=self.source, **{k: v for k, v in f.items() if k != "tags"})
            for f in self.data.get("findings", [])
            if self._project(f, values)
        ]

    async def health_check(self) -> dict[str, str]:
        return {"mode": "demo"}

    async def close(self) -> None:
        return None


# ------------------------------------------------------------------ specs


def _aws(s: Settings) -> AwsCloudService:
    return AwsCloudService(
        lambda: AwsClient(
            s.aws_access_key_id,
            s.aws_secret_access_key.get_secret_value(),
            s.aws_session_token.get_secret_value(),
            timeout=s.connector_timeout_seconds,
        ),
        s.aws_regions,
        s.cloud_project_tag_key,
        cost_explorer=s.aws_cost_explorer_enabled,
    )


def _azure(s: Settings) -> AzureCloudService:
    token = azure_management_token(s)
    return AzureCloudService(
        lambda: ConnectorClient(
            "Azure",
            base_url=AzureCloudService.base,
            timeout=s.connector_timeout_seconds,
            token_provider=token,
        ),
        s.azure_subscription_id,
        s.cloud_project_tag_key,
    )


def _demo(source: str):
    def build(data: dict[str, Any], today: date) -> DemoCloud:
        from backend.app.integrations.demo.services import _resolve

        cloud = (data.get("cloud") or {}).get(source) or {}
        return DemoCloud(source, _resolve(cloud, today), today)

    return build


def _gcp(s: Settings) -> GcpCloudService:
    from backend.app.integrations.connectors.auth import GoogleServiceAccountToken

    token = GoogleServiceAccountToken(
        s.google_service_account_file,  # type: ignore[arg-type]
        ["https://www.googleapis.com/auth/cloud-platform"],
    )
    return GcpCloudService(
        lambda: ConnectorClient(
            "Google Cloud",
            base_url="https://cloudasset.googleapis.com",
            timeout=s.connector_timeout_seconds,
            token_provider=token,
        ),
        s.gcp_project_ids,
        s.cloud_project_tag_key,
    )


CLOUD_SPECS = (
    ConnectorSpec(
        "aws",
        "Amazon Web Services",
        "cloud",
        required=("aws_access_key_id", "aws_secret_access_key"),
        optional=(
            "aws_session_token",
            "aws_regions",
            "aws_cost_explorer_enabled",
            "cloud_project_tag_key",
        ),
        setup="IAM user or role with a read-only policy: tag:GetResources, ec2:DescribeInstances, "
        "ec2:DescribeInstanceStatus, ec2:DescribeVolumes, rds:DescribeDBInstances, "
        "cloudwatch:DescribeAlarms, cloudwatch:GetMetricStatistics, securityhub:GetFindings "
        "(+ ce:GetCostAndUsage, ce:GetCostForecast if AWS_COST_EXPLORER_ENABLED). Tag resources "
        "project=<KEY> (and purpose / environment / owner)",
        docs_url="https://docs.aws.amazon.com/resourcegroupstagging/latest/APIReference/API_GetResources.html",
        build=_aws,
        demo=_demo("aws"),
    ),
    ConnectorSpec(
        "azure",
        "Microsoft Azure",
        "cloud",
        required=(
            "azure_tenant_id",
            "azure_client_id",
            "azure_client_secret",
            "azure_subscription_id",
        ),
        optional=("cloud_project_tag_key",),
        setup="Entra ID app registration (client secret) with Reader, Cost Management Reader, "
        "Monitoring Reader and Security Reader on the subscription. Tag resources "
        "project=<KEY> (and purpose / environment / owner)",
        docs_url="https://learn.microsoft.com/azure/governance/resource-graph/overview",
        build=_azure,
        demo=_demo("azure"),
    ),
    ConnectorSpec(
        "gcp",
        "Google Cloud",
        "cloud",
        required=("google_service_account_file", "gcp_project_ids"),
        optional=("cloud_project_tag_key",),
        setup="Service account key (GOOGLE_SERVICE_ACCOUNT_FILE) with Cloud Asset Viewer, Monitoring "
        "Viewer and Security Center Findings Viewer on each project in GCP_PROJECT_IDS. Label "
        "resources project=<key in lowercase> (and purpose / environment / owner). Cost needs a "
        "BigQuery billing export and is not shown",
        docs_url="https://cloud.google.com/asset-inventory/docs/reference/rest/v1/TopLevel/searchAllResources",
        build=_gcp,
        demo=_demo("gcp"),
    ),
)
