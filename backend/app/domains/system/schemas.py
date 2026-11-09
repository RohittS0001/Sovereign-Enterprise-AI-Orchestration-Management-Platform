from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["healthy"]
    service: str
    environment: str
    version: str


class DependencyStatus(BaseModel):
    name: str
    configured: bool
    # None when no live check is performed for this dependency.
    healthy: bool | None = None


class ReadinessResponse(BaseModel):
    status: Literal["ready", "degraded"]
    dependencies: list[DependencyStatus]
