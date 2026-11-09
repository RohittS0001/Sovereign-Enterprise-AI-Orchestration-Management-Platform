from backend.app.core.config.settings import Settings
from backend.app.db.session import Database
from backend.app.schemas.health import DependencyStatus, HealthResponse, ReadinessResponse


def get_health_status(settings: Settings) -> HealthResponse:
    """Liveness: the process is up and serving requests."""
    return HealthResponse(
        status="healthy",
        service="backend",
        environment=settings.environment,
        version=settings.version,
    )


async def get_readiness_status(settings: Settings, database: Database | None) -> ReadinessResponse:
    """Readiness: is each dependency configured, and is the database reachable?

    External integrations are only checked for configuration so the probe stays fast
    and never calls third-party services.
    """
    database_ok = database is not None and await database.ping()
    dependencies = [
        DependencyStatus(name="database", configured=database is not None, healthy=database_ok),
        DependencyStatus(name="jira", configured=settings.jira_configured),
        DependencyStatus(name="gmail", configured=settings.gmail_configured),
    ]
    ready = all(d.configured and d.healthy is not False for d in dependencies)
    return ReadinessResponse(status="ready" if ready else "degraded", dependencies=dependencies)
