from backend.app.db.models import Project
from backend.app.integrations.demo.seed import PHX_CONFIG

__all__ = ["PHX_CONFIG", "phx_project"]


def phx_project() -> Project:
    return Project(key="PHX", **PHX_CONFIG, is_active=True)
