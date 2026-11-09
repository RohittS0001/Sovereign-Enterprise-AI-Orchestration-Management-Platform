from backend.app.core.config.settings import Settings
from backend.app.schemas.health import DependencyStatus, HealthResponse, ReadinessResponse


def get_health_status(settings: Settings) -> HealthResponse:
    """Liveness: the process is up and serving requests."""
    return HealthResponse(
        status="healthy",
        service="backend",
        environment=settings.environment,
        version=settings.version,
    )


def get_readiness_status(settings: Settings) -> ReadinessResponse:
    """Readiness: report which dependencies are configured.

    Only configuration is checked here, so the probe stays fast and never calls
    external services. Live connectivity checks come with the integration layer.
    """
    dependencies = [
        DependencyStatus(name="jira", configured=settings.jira_configured),
        DependencyStatus(name="gmail", configured=settings.gmail_configured),
        DependencyStatus(name="database", configured=bool(settings.database_url)),
    ]
    status = "ready" if all(d.configured for d in dependencies) else "degraded"
    return ReadinessResponse(status=status, dependencies=dependencies)
