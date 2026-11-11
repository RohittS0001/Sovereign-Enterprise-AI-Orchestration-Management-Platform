"""Engineering operations: incidents, errors, deployments and quality gates.

PagerDuty and Opsgenie (incidents), Sentry (error groups), Jenkins and Argo CD
(deployments / builds), SonarQube or SonarCloud (quality gates). A service implements only
the capabilities its system has; tools and analytics call whichever exist. Note that
Atlassian is retiring Opsgenie (migrating to Jira Service Management).
"""

import re
from typing import Any
from urllib.parse import quote

from backend.app.core.config import Settings
from backend.app.integrations.connectors.base import (
    ConnectorClient,
    ConnectorService,
    Deployment,
    ErrorGroup,
    Incident,
    QualityGate,
    safe_id,
    to_date,
)
from backend.app.integrations.connectors.spec import ConnectorSpec

CAPABILITIES = ("list_incidents", "list_errors", "list_deployments", "quality_gates")


def capabilities(service: Any) -> list[str]:
    return [c for c in CAPABILITIES if callable(getattr(service, c, None))]


# ------------------------------------------------------------------ PagerDuty


class PagerDutyService(ConnectorService):
    source = "pagerduty"

    def __init__(self, client_factory: Any, services: list[str] | None = None) -> None:
        super().__init__(client_factory)
        self.services = services or []

    async def list_incidents(
        self, open_only: bool = True, scope: list[str] | None = None, limit: int = 25
    ) -> list[Incident]:
        params: dict[str, Any] = {"limit": limit}
        if open_only:
            params["statuses[]"] = ["triggered", "acknowledged"]
        ids = [safe_id(s, "PagerDuty service") for s in (scope or self.services)]
        if ids:
            params["service_ids[]"] = ids
        body = await self.client.get("/incidents", params)
        return [
            Incident(
                source=self.source,
                id=str(i.get("incident_number") or i.get("id")),
                title=i.get("title", ""),
                status=i.get("status", ""),
                severity=i.get("urgency"),
                service=(i.get("service") or {}).get("summary"),
                created=to_date(i.get("created_at")),
                url=i.get("html_url"),
                resolved=to_date(i.get("resolved_at")),
            )
            for i in body.get("incidents", [])
        ]

    async def health_check(self) -> dict[str, Any]:
        return await self.client.get("/abilities")


# ------------------------------------------------------------------ Opsgenie


_TAG = re.compile(r"[A-Za-z0-9._-]{1,100}")


def opsgenie_tag(value: str) -> str:
    """Tags go into Opsgenie's query language, so no spaces, colons or operators."""
    if not _TAG.fullmatch(value):
        raise ValueError(f"Invalid Opsgenie tag: {value!r}")
    return value


class OpsgenieService(ConnectorService):
    source = "opsgenie"

    async def list_incidents(
        self, open_only: bool = True, scope: list[str] | None = None, limit: int = 25
    ) -> list[Incident]:
        terms = ["status:open"] if open_only else []
        terms += [f"tag:{opsgenie_tag(t)}" for t in scope or []]
        body = await self.client.get("/v2/alerts", {"query": " AND ".join(terms), "limit": limit})
        return [
            Incident(
                source=self.source,
                id=str(a.get("tinyId") or a.get("id")),
                title=a.get("message", ""),
                status=a.get("status", ""),
                severity=a.get("priority"),
                created=to_date(a.get("createdAt")),
                service=", ".join(a.get("tags") or []) or None,
            )
            for a in body.get("data", [])
        ]

    async def health_check(self) -> dict[str, Any]:
        return await self.client.get("/v2/account")


# ------------------------------------------------------------------ Sentry


class SentryService(ConnectorService):
    source = "sentry"

    def __init__(self, client_factory: Any, org: str) -> None:
        super().__init__(client_factory)
        self.org = org

    async def list_errors(
        self, scope: list[str] | None = None, limit: int = 25
    ) -> list[ErrorGroup]:
        if not scope:
            raise ValueError("Sentry needs project slugs (project links: sentry_projects)")
        groups: list[ErrorGroup] = []
        org = safe_id(self.org, "Sentry organization")
        for project in scope:
            rows = await self.client.get(
                f"/projects/{org}/{safe_id(project, 'Sentry project')}/issues/",
                {"query": "is:unresolved", "statsPeriod": "14d", "limit": limit},
            )
            groups += [
                ErrorGroup(
                    source=self.source,
                    id=str(r.get("shortId") or r.get("id")),
                    title=r.get("title", ""),
                    level=r.get("level"),
                    events=int(r.get("count") or 0),
                    users=int(r.get("userCount") or 0),
                    last_seen=to_date(r.get("lastSeen")),
                    url=r.get("permalink"),
                )
                for r in rows
            ]
        return sorted(groups, key=lambda g: -g.events)[:limit]

    async def health_check(self) -> dict[str, Any]:
        body = await self.client.get(f"/organizations/{safe_id(self.org, 'Sentry organization')}/")
        return {"organization": body.get("slug")}


# ------------------------------------------------------------------ Jenkins

_JENKINS_RESULT = {
    "SUCCESS": "success",
    "FAILURE": "failure",
    "UNSTABLE": "failure",
    "ABORTED": "aborted",
    "NOT_BUILT": "not_built",
}


def jenkins_job_path(name: str) -> str:
    """'folder/job' → /job/folder/job/job (Jenkins folder URLs)."""
    parts = [safe_id(p, "Jenkins job") for p in name.strip("/").split("/")]
    return "".join(f"/job/{quote(p)}" for p in parts)


class JenkinsService(ConnectorService):
    source = "jenkins"

    def __init__(self, client_factory: Any, jobs: list[str] | None = None) -> None:
        super().__init__(client_factory)
        self.jobs = jobs or []

    async def list_deployments(
        self, scope: list[str] | None = None, limit: int = 25
    ) -> list[Deployment]:
        jobs = scope or self.jobs
        if not jobs:
            raise ValueError(
                "Jenkins needs job names (JENKINS_JOBS or project links: jenkins_jobs)"
            )
        results = []
        for job in jobs[:limit]:
            build = await self.client.get(
                f"{jenkins_job_path(job)}/lastBuild/api/json",
                {"tree": "number,result,timestamp,url,building"},
            )
            status = (
                "running"
                if build.get("building")
                else _JENKINS_RESULT.get(build.get("result") or "", "unknown")
            )
            results.append(
                Deployment(
                    source=self.source,
                    name=job,
                    status=status,
                    finished=to_date(build.get("timestamp")),
                    url=build.get("url"),
                    detail=f"build #{build.get('number')}",
                )
            )
        return results

    async def health_check(self) -> dict[str, Any]:
        return await self.client.get("/api/json", {"tree": "mode"})


# ------------------------------------------------------------------ Argo CD


class ArgoCDService(ConnectorService):
    source = "argocd"

    async def list_deployments(
        self, scope: list[str] | None = None, limit: int = 25
    ) -> list[Deployment]:
        body = await self.client.get("/api/v1/applications")
        wanted = {safe_id(s, "Argo CD application") for s in scope or []}
        results = []
        for app in body.get("items") or []:
            name = (app.get("metadata") or {}).get("name", "")
            if wanted and name not in wanted:
                continue
            status = app.get("status") or {}
            health = (status.get("health") or {}).get("status", "Unknown")
            sync = (status.get("sync") or {}).get("status", "Unknown")
            state = (
                "failure"
                if health in {"Degraded", "Missing"}
                else "running"
                if health == "Progressing"
                else "out_of_sync"
                if sync == "OutOfSync"
                else "success"
                if health == "Healthy"
                else "unknown"
            )
            results.append(
                Deployment(
                    source=self.source,
                    name=name,
                    status=state,
                    detail=f"{health}, {sync}",
                    finished=to_date((status.get("operationState") or {}).get("finishedAt")),
                )
            )
        return results[:limit]

    async def health_check(self) -> dict[str, Any]:
        return await self.client.get("/api/version")


# ------------------------------------------------------------------ SonarQube / SonarCloud


class SonarQubeService(ConnectorService):
    source = "sonarqube"

    async def quality_gates(self, scope: list[str] | None = None) -> list[QualityGate]:
        if not scope:
            raise ValueError("SonarQube needs project keys (project links: sonarqube_projects)")
        gates = []
        for key in scope:
            body = await self.client.get(
                "/api/qualitygates/project_status",
                {"projectKey": safe_id(key, "SonarQube project")},
            )
            status = body.get("projectStatus") or {}
            failing = [
                f"{c.get('metricKey')} = {c.get('actualValue')} (threshold {c.get('errorThreshold')})"
                for c in status.get("conditions") or []
                if c.get("status") == "ERROR"
            ]
            gates.append(
                QualityGate(
                    source=self.source,
                    project=key,
                    status=status.get("status", "NONE"),
                    failing=failing,
                )
            )
        return gates

    async def health_check(self) -> dict[str, Any]:
        body = await self.client.get("/api/authentication/validate")
        if not body.get("valid", False):
            raise ValueError("SonarQube token is not valid")
        return body


# ------------------------------------------------------------------ Datadog


class DatadogService(ConnectorService):
    """Datadog incidents and monitors in an alert state, scoped by service tag."""

    source = "datadog"

    async def list_incidents(
        self, open_only: bool = True, scope: list[str] | None = None, limit: int = 25
    ) -> list[Incident]:
        services = {safe_id(s, "Datadog service") for s in scope or []}
        body = await self.client.get("/api/v2/incidents", {"page[size]": min(limit, 100)})
        incidents = []
        for item in body.get("data") or []:
            attrs = item.get("attributes") or {}
            fields = attrs.get("fields") or {}
            tagged = (fields.get("services") or {}).get("value") or []
            if services and not services & set(tagged):
                continue
            state = str((fields.get("state") or {}).get("value") or attrs.get("state") or "active")
            if open_only and state.lower() == "resolved":
                continue
            incidents.append(
                Incident(
                    source=self.source,
                    id=str(attrs.get("public_id") or item.get("id")),
                    title=attrs.get("title", ""),
                    status=state.lower(),
                    severity=str(
                        (fields.get("severity") or {}).get("value") or attrs.get("severity") or ""
                    )
                    or None,
                    service=", ".join(tagged) or None,
                    created=to_date(attrs.get("created")),
                    resolved=to_date(attrs.get("resolved")),
                )
            )
        # Monitors currently alerting count as open incidents too.
        params: dict[str, Any] = {"group_states": "alert", "page_size": min(limit, 100)}
        if services:
            params["monitor_tags"] = ",".join(f"service:{s}" for s in sorted(services))
        for monitor in await self.client.get("/api/v1/monitor", params) or []:
            if str(monitor.get("overall_state")).lower() not in {"alert", "warn"}:
                continue
            tags = [
                t.split(":", 1)[1] for t in monitor.get("tags") or [] if t.startswith("service:")
            ]
            incidents.append(
                Incident(
                    source=self.source,
                    id=f"monitor-{monitor.get('id')}",
                    title=f"Monitor: {monitor.get('name', '')}",
                    status="triggered",
                    severity="high" if monitor.get("overall_state") == "Alert" else "low",
                    service=", ".join(tags) or None,
                    created=to_date(monitor.get("overall_state_modified")),
                )
            )
        return incidents[:limit]

    async def health_check(self) -> dict[str, Any]:
        return await self.client.get("/api/v1/validate")


# ------------------------------------------------------------------ ServiceNow

_SN_VALUE = re.compile(r"^[A-Za-z0-9 ._/()-]{1,100}$")


def servicenow_value(value: str) -> str:
    """Values go into an encoded query: no ^ (AND/OR), commas or = allowed."""
    if not _SN_VALUE.fullmatch(value):
        raise ValueError(f"Invalid ServiceNow value: {value!r}")
    return value


class ServiceNowService(ConnectorService):
    """ServiceNow ITSM: active incidents and implemented change requests per service (CI)."""

    source = "servicenow"

    def _scope(self, scope: list[str] | None) -> str:
        names = [servicenow_value(s) for s in scope or []]
        return f"^cmdb_ci.nameIN{','.join(names)}" if names else ""

    async def list_incidents(
        self, open_only: bool = True, scope: list[str] | None = None, limit: int = 25
    ) -> list[Incident]:
        query = ("active=true" if open_only else "sys_idISNOTEMPTY") + self._scope(scope)
        query += "^ORDERBYDESCopened_at"
        body = await self.client.get(
            "/api/now/table/incident",
            {
                "sysparm_query": query,
                "sysparm_limit": limit,
                "sysparm_display_value": "true",
                "sysparm_fields": "sys_id,number,short_description,state,priority,cmdb_ci,opened_at,resolved_at",
            },
        )
        return [
            Incident(
                source=self.source,
                id=r.get("number") or r.get("sys_id", ""),
                title=r.get("short_description", ""),
                status=str(r.get("state") or "").lower(),
                severity=str(r.get("priority") or "").split(" - ")[-1].lower() or None,
                service=_display(r.get("cmdb_ci")),
                created=to_date(r.get("opened_at")),
                resolved=to_date(r.get("resolved_at")),
            )
            for r in body.get("result") or []
        ]

    async def list_deployments(
        self, scope: list[str] | None = None, limit: int = 25
    ) -> list[Deployment]:
        """Closed change requests = production changes (successful or not), for DORA."""
        body = await self.client.get(
            "/api/now/table/change_request",
            {
                "sysparm_query": "state=3" + self._scope(scope) + "^ORDERBYDESCclosed_at",
                "sysparm_limit": limit,
                "sysparm_display_value": "true",
                "sysparm_fields": "number,short_description,close_code,closed_at,cmdb_ci",
            },
        )
        return [
            Deployment(
                source=self.source,
                name=f"{_display(r.get('cmdb_ci')) or 'change'} production",
                status="success"
                if str(r.get("close_code") or "").lower().startswith("successful")
                else "failure",
                finished=to_date(r.get("closed_at")),
                detail=f"{r.get('number')}: {r.get('short_description', '')}"[:200],
            )
            for r in body.get("result") or []
        ]

    async def health_check(self) -> dict[str, Any]:
        body = await self.client.get(
            "/api/now/table/sys_user", {"sysparm_limit": 1, "sysparm_fields": "sys_id"}
        )
        return {"ok": bool(body.get("result") is not None)}


def _display(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("display_value") or value.get("value") or None
    return value or None


# ------------------------------------------------------------------ demo twin


class DemoOps:
    def __init__(self, source: str, data: dict[str, Any]) -> None:
        self.source = source
        self.data = data
        for capability, key in (
            ("list_incidents", "incidents"),
            ("list_errors", "errors"),
            ("list_deployments", "deployments"),
            ("quality_gates", "quality_gates"),
        ):
            if key not in data:  # only expose what the real system has
                setattr(self, capability, None)

    def _rows(self, key: str, model: type, scope: list[str] | None, field: str) -> list[Any]:
        rows = [model(source=self.source, **r) for r in self.data.get(key, [])]
        return [r for r in rows if not scope or getattr(r, field) in scope]

    async def list_incidents(
        self, open_only: bool = True, scope: list[str] | None = None, limit: int = 25
    ) -> list[Incident]:
        rows = self._rows("incidents", Incident, scope, "service")
        return [r for r in rows if not open_only or r.status not in {"resolved", "closed"}][:limit]

    async def list_errors(
        self, scope: list[str] | None = None, limit: int = 25
    ) -> list[ErrorGroup]:
        return [
            ErrorGroup(source=self.source, **{k: v for k, v in r.items() if k != "project"})
            for r in self.data.get("errors", [])
            if not scope or r.get("project") in scope
        ][:limit]

    async def list_deployments(
        self, scope: list[str] | None = None, limit: int = 25
    ) -> list[Deployment]:
        return self._rows("deployments", Deployment, scope, "name")[:limit]

    async def quality_gates(self, scope: list[str] | None = None) -> list[QualityGate]:
        return self._rows("quality_gates", QualityGate, scope, "project")

    async def health_check(self) -> dict[str, str]:
        return {"mode": "demo"}

    async def close(self) -> None:
        return None


def _demo(source: str) -> Any:
    return lambda data, today: DemoOps(source, data.get("ops", {}).get(source, {}))


def _client(s: Settings, name: str, base_url: str, **kwargs: Any) -> Any:
    return lambda: ConnectorClient(
        name, base_url=base_url, timeout=s.connector_timeout_seconds, **kwargs
    )


# Which project link (Project.links key) scopes each source.
PROJECT_LINKS = {
    "pagerduty": "pagerduty_services",
    "opsgenie": "opsgenie_tags",
    "sentry": "sentry_projects",
    "jenkins": "jenkins_jobs",
    "argocd": "argocd_apps",
    "sonarqube": "sonarqube_projects",
    "datadog": "datadog_services",
    "servicenow": "servicenow_services",
}

OPS_SPECS = (
    ConnectorSpec(
        "pagerduty",
        "PagerDuty",
        "ops",
        required=("pagerduty_api_key",),
        optional=("pagerduty_services",),
        setup="PagerDuty → Integrations → API Access Keys (read-only key)",
        docs_url="https://developer.pagerduty.com/api-reference/",
        build=lambda s: PagerDutyService(
            _client(
                s,
                "PagerDuty",
                "https://api.pagerduty.com",
                headers={
                    "Authorization": f"Token token={s.pagerduty_api_key.get_secret_value()}",
                    "Accept": "application/vnd.pagerduty+json;version=2",
                },
            ),
            s.pagerduty_services,
        ),
        demo=_demo("pagerduty"),
    ),
    ConnectorSpec(
        "opsgenie",
        "Opsgenie",
        "ops",
        required=("opsgenie_api_key",),
        optional=("opsgenie_api_url",),
        setup="Opsgenie → Settings → API key management (read access); EU accounts set OPSGENIE_API_URL",
        docs_url="https://docs.opsgenie.com/docs/alert-api",
        build=lambda s: OpsgenieService(
            _client(
                s,
                "Opsgenie",
                s.opsgenie_api_url,
                headers={"Authorization": f"GenieKey {s.opsgenie_api_key.get_secret_value()}"},
            )
        ),
        demo=_demo("opsgenie"),
    ),
    ConnectorSpec(
        "sentry",
        "Sentry",
        "ops",
        required=("sentry_token", "sentry_org"),
        optional=("sentry_url",),
        setup="Sentry → Settings → Auth Tokens (scopes: project:read, event:read); self-hosted: SENTRY_URL",
        docs_url="https://docs.sentry.io/api/",
        build=lambda s: SentryService(
            _client(
                s,
                "Sentry",
                f"{s.sentry_url.rstrip('/')}/api/0",
                headers={"Authorization": f"Bearer {s.sentry_token.get_secret_value()}"},
            ),
            s.sentry_org,
        ),
        demo=_demo("sentry"),
    ),
    ConnectorSpec(
        "jenkins",
        "Jenkins",
        "ops",
        required=("jenkins_url", "jenkins_username", "jenkins_api_token"),
        optional=("jenkins_jobs",),
        setup="Jenkins → your user → Configure → API Token",
        docs_url="https://www.jenkins.io/doc/book/using/remote-access-api/",
        build=lambda s: JenkinsService(
            _client(
                s,
                "Jenkins",
                s.jenkins_url,
                auth=(s.jenkins_username, s.jenkins_api_token.get_secret_value()),
            ),
            s.jenkins_jobs,
        ),
        demo=_demo("jenkins"),
    ),
    ConnectorSpec(
        "argocd",
        "Argo CD",
        "ops",
        required=("argocd_url", "argocd_token"),
        setup="argocd account generate-token --account <read-only account>",
        docs_url="https://argo-cd.readthedocs.io/en/stable/developer-guide/api-docs/",
        build=lambda s: ArgoCDService(
            _client(
                s,
                "Argo CD",
                s.argocd_url,
                headers={"Authorization": f"Bearer {s.argocd_token.get_secret_value()}"},
            )
        ),
        demo=_demo("argocd"),
    ),
    ConnectorSpec(
        "sonarqube",
        "SonarQube / SonarCloud",
        "ops",
        required=("sonarqube_token",),
        optional=("sonarqube_url",),
        setup="My Account → Security → Generate token (Browse permission); self-hosted: SONARQUBE_URL",
        docs_url="https://docs.sonarsource.com/sonarqube-server/latest/extension-guide/web-api/",
        build=lambda s: SonarQubeService(
            _client(
                s,
                "SonarQube",
                s.sonarqube_url,
                headers={"Authorization": f"Bearer {s.sonarqube_token.get_secret_value()}"},
            )
        ),
        demo=_demo("sonarqube"),
    ),
    ConnectorSpec(
        "datadog",
        "Datadog",
        "ops",
        required=("datadog_api_key", "datadog_app_key"),
        optional=("datadog_site",),
        setup="Datadog → Organization Settings → API Keys + Application Keys (scopes incident_read, "
        "monitors_read); EU or other regions set DATADOG_SITE (e.g. datadoghq.eu)",
        docs_url="https://docs.datadoghq.com/api/latest/",
        build=lambda s: DatadogService(
            _client(
                s,
                "Datadog",
                f"https://api.{s.datadog_site}",
                headers={
                    "DD-API-KEY": s.datadog_api_key.get_secret_value(),
                    "DD-APPLICATION-KEY": s.datadog_app_key.get_secret_value(),
                },
            )
        ),
        demo=_demo("datadog"),
    ),
    ConnectorSpec(
        "servicenow",
        "ServiceNow",
        "ops",
        required=("servicenow_instance_url", "servicenow_username", "servicenow_password"),
        setup="A ServiceNow user with the itil (read) role; instance URL https://<company>.service-now.com",
        docs_url="https://developer.servicenow.com/dev.do#!/reference/api/latest/rest/c_TableAPI",
        build=lambda s: ServiceNowService(
            _client(
                s,
                "ServiceNow",
                s.servicenow_instance_url,
                auth=(s.servicenow_username, s.servicenow_password.get_secret_value()),
            )
        ),
        demo=_demo("servicenow"),
    ),
)
