from backend.app.core.config.settings import settings


def get_health_status() -> dict[str, str]:
    return {
        "status": "healthy",
        "service": "backend",
        "environment": settings.environment,
    }