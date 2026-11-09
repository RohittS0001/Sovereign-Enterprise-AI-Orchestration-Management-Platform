from typing import Any

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Project(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Links a Jira project to its repositories, current sprint and team.

    ``members`` entries: ``{"name", "jira_account_id", "keka_employee_id",
    "capacity_points"}`` — used to join Jira assignments with Keka leave.
    """

    __tablename__ = "projects"

    key: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # Jira project key
    name: Mapped[str] = mapped_column(String(200))
    github_repos: Mapped[list[str]] = mapped_column(default=list)
    default_branch: Mapped[str] = mapped_column(String(100), default="main")
    active_sprint_id: Mapped[int | None]
    members: Mapped[list[dict[str, Any]]] = mapped_column(default=list)
    report_recipients: Mapped[list[str]] = mapped_column(default=list)
    is_active: Mapped[bool] = mapped_column(default=True)
