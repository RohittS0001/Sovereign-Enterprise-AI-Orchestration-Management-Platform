"""Ops connectors against mocked vendor APIs, and the production-health risk factor."""

from datetime import date

import pytest

from backend.app.domains.analytics.risk import production_factor
from backend.app.domains.analytics.snapshot import OpsFacts, ProjectSnapshot
from backend.app.integrations.connectors.base import Deployment, ErrorGroup, Incident, QualityGate
from backend.app.integrations.connectors.ops import (
    ArgoCDService,
    JenkinsService,
    OpsgenieService,
    PagerDutyService,
    SentryService,
    SonarQubeService,
    capabilities,
    jenkins_job_path,
)
from tests.support.connectors import MockApi


async def test_pagerduty_filters_open_incidents_by_service() -> None:
    api = MockApi({"GET /incidents": {"incidents": [
        {"id": "Q1", "incident_number": 42, "title": "Latency", "status": "triggered",
         "urgency": "high", "service": {"summary": "payments"}, "created_at": "2026-11-09T08:00:00Z",
         "html_url": "https://acme.pagerduty.com/incidents/Q1"}]}})  # fmt: skip
    svc = PagerDutyService(api.factory("PagerDuty", "https://api.pagerduty.com",
                                       headers={"Authorization": "Token token=k"}))  # fmt: skip

    [incident] = await svc.list_incidents(scope=["PSVC1"])

    assert api.last.url.params.get_list("statuses[]") == ["triggered", "acknowledged"]
    assert api.last.url.params.get_list("service_ids[]") == ["PSVC1"]
    assert api.last.headers["authorization"] == "Token token=k"
    assert (incident.id, incident.severity, incident.service) == ("42", "high", "payments")
    assert capabilities(svc) == ["list_incidents"]


async def test_opsgenie_builds_a_safe_query() -> None:
    api = MockApi({"GET /v2/alerts": {"data": [
        {"tinyId": "7", "message": "Disk full", "status": "open", "priority": "P1",
         "tags": ["payments"], "createdAt": "2026-11-09T00:00:00Z"}]}})  # fmt: skip
    svc = OpsgenieService(api.factory("Opsgenie", "https://api.opsgenie.com"))

    [alert] = await svc.list_incidents(scope=["payments"])

    assert api.last.url.params["query"] == "status:open AND tag:payments"
    assert (alert.id, alert.severity) == ("7", "P1")
    with pytest.raises(ValueError):
        await svc.list_incidents(scope=["x OR status:closed\n"])


async def test_sentry_sorts_error_groups_by_volume() -> None:
    rows = [{"id": "1", "shortId": "API-1", "title": "Rare", "count": "3", "userCount": 1},
            {"id": "2", "shortId": "API-2", "title": "Noisy", "count": "900", "userCount": 50,
             "level": "error", "lastSeen": "2026-11-09T10:00:00Z"}]  # fmt: skip
    api = MockApi({"GET /api/0/projects/acme/payments-api/issues/": rows})
    svc = SentryService(api.factory("Sentry", "https://sentry.io/api/0"), org="acme")

    groups = await svc.list_errors(scope=["payments-api"])

    assert [g.id for g in groups] == ["API-2", "API-1"] and groups[0].events == 900
    assert api.last.url.params["query"] == "is:unresolved"
    with pytest.raises(ValueError, match="project slugs"):
        await svc.list_errors()


async def test_jenkins_folder_paths_and_build_results() -> None:
    assert jenkins_job_path("payments/release") == "/job/payments/job/release"
    with pytest.raises(ValueError):
        jenkins_job_path("../../script")
    api = MockApi({
        "GET /job/payments/job/release/lastBuild/api/json":
            {"number": 12, "result": "UNSTABLE", "timestamp": 1762646400000, "building": False},
        "GET /job/web/lastBuild/api/json": {"number": 5, "result": None, "building": True},
    })  # fmt: skip
    svc = JenkinsService(api.factory("Jenkins", "https://ci.example.com", auth=("u", "t")))

    builds = await svc.list_deployments(scope=["payments/release", "web"])

    assert [(b.name, b.status) for b in builds] == [
        ("payments/release", "failure"),
        ("web", "running"),
    ]


async def test_argocd_maps_health_and_sync() -> None:
    api = MockApi({"GET /api/v1/applications": {"items": [
        {"metadata": {"name": "api-prod"}, "status": {"health": {"status": "Healthy"}, "sync": {"status": "Synced"}}},
        {"metadata": {"name": "api-stg"}, "status": {"health": {"status": "Degraded"}, "sync": {"status": "Synced"}}},
        {"metadata": {"name": "web"}, "status": {"health": {"status": "Healthy"}, "sync": {"status": "OutOfSync"}}},
        {"metadata": {"name": "other-team"}, "status": {}},
    ]}})  # fmt: skip
    svc = ArgoCDService(api.factory("Argo CD", "https://argocd.example.com"))

    apps = await svc.list_deployments(scope=["api-prod", "api-stg", "web"])

    assert [(a.name, a.status) for a in apps] == [
        ("api-prod", "success"),
        ("api-stg", "failure"),
        ("web", "out_of_sync"),
    ]


async def test_sonarqube_reports_failing_conditions() -> None:
    api = MockApi({
        "GET /api/qualitygates/project_status": {"projectStatus": {"status": "ERROR", "conditions": [
            {"metricKey": "new_coverage", "status": "ERROR", "actualValue": "61.2", "errorThreshold": "80"},
            {"metricKey": "new_bugs", "status": "OK", "actualValue": "0"}]}},
        "GET /api/authentication/validate": {"valid": False},
    })  # fmt: skip
    svc = SonarQubeService(api.factory("SonarQube", "https://sonarcloud.io"))

    [gate] = await svc.quality_gates(scope=["acme_payments"])

    assert gate.status == "ERROR" and gate.failing == ["new_coverage = 61.2 (threshold 80)"]
    assert api.last.url.params["projectKey"] == "acme_payments"
    with pytest.raises(ValueError, match="not valid"):
        await svc.health_check()


def test_production_factor_weights_and_evidence() -> None:
    snap = ProjectSnapshot(key="P", name="P", today=date(2026, 11, 9), ops=OpsFacts(
        incidents=[Incident(source="pagerduty", id="1", title="Down", status="triggered", severity="high"),
                   Incident(source="opsgenie", id="2", title="Slow", status="open", severity="P4")],
        errors=[ErrorGroup(source="sentry", id="E", title="Boom", events=150, users=9),
                ErrorGroup(source="sentry", id="F", title="Meh", events=5)],
        deployments=[Deployment(source="argocd", name="stg", status="failure")],
        quality_gates=[QualityGate(source="sonarqube", project="p", status="ERROR", failing=["x"])],
    ))  # fmt: skip

    factor = production_factor(snap)

    # 5 (high incident) + 2 (other) + 4 (gate) + 3 (deploy) + 1 (noisy error) = 15
    assert factor.score == 15 and factor.max_score == 15
    assert any("Quality gate failing on p" in e for e in factor.evidence)
    assert not any("Meh" in e for e in factor.evidence)  # quiet errors are not evidence
    quiet = production_factor(
        ProjectSnapshot(key="Q", name="Q", today=date(2026, 11, 9), ops=OpsFacts())
    )
    assert quiet.score == 0 and quiet.recommendation is None
