"""Deployment configuration checks (Docker/Helm are not runnable on the dev machine; CI also
runs `helm lint` and `docker compose config`): nginx header inheritance, probes, metrics auth."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
NGINX = (ROOT / "deploy/docker/nginx.conf.template").read_text(encoding="utf-8")
HELM = ROOT / "deploy/helm/sovereign"
SECURITY_HEADERS = ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy")


def locations(conf: str) -> dict[str, str]:
    """location blocks (no nesting in this file) → their bodies."""
    return {
        m.group(1).strip(): m.group(2) for m in re.finditer(r"location\s+([^{]+)\{([^}]*)\}", conf)
    }


def test_every_location_that_adds_headers_repeats_the_security_headers() -> None:
    blocks = locations(NGINX)
    assert "/assets/" in blocks and "/" in blocks
    for name, body in blocks.items():
        if "add_header" in body:  # nginx then drops the server-level add_header directives
            missing = [h for h in SECURITY_HEADERS if h not in body]
            assert not missing, f"location {name} loses {missing}"
    server_level = re.split(r"^\s*location\s", NGINX, maxsplit=1, flags=re.MULTILINE)[0]
    assert "location = /healthz" not in server_level
    assert all(h in server_level for h in SECURITY_HEADERS)
    assert "return 404" in blocks["= /metrics"]  # metrics never served through the web tier


def test_readiness_and_liveness_use_different_endpoints() -> None:
    api = (HELM / "templates/api.yaml").read_text(encoding="utf-8")
    readiness = re.search(r"readinessProbe:\s*\n\s*httpGet: \{path: ([^,]+),", api).group(1)
    liveness = re.search(r"livenessProbe:\s*\n\s*httpGet: \{path: ([^,]+),", api).group(1)
    assert readiness == "/api/v1/health/ready" and liveness == "/api/v1/health"


def test_service_monitor_authenticates_and_refuses_to_render_without_a_token() -> None:
    template = (HELM / "templates/ingress.yaml").read_text(encoding="utf-8")
    monitor = template.split("kind: ServiceMonitor", 1)[1]
    assert "authorization:" in monitor and "type: Bearer" in monitor
    assert "tokenSecret.key" in monitor
    assert 'fail "metrics.serviceMonitor needs tokenSecret.key' in template
    values = (HELM / "values.yaml").read_text(encoding="utf-8")
    assert "key: METRICS_TOKEN" in values and "METRICS_TOKEN=" in values


def test_redis_url_is_configurable_in_every_deployment() -> None:
    values = (HELM / "values.yaml").read_text(encoding="utf-8")
    assert 'REDIS_URL: ""' in values
    assert "REDIS_URL=" in (ROOT / ".env.example").read_text(encoding="utf-8")
