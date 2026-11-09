import uuid
from datetime import date
from typing import Any

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
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


class Report(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "reports"
    __table_args__ = (
        # One scheduled report per project/kind/period, even with several API workers.
        UniqueConstraint(
            "project_key", "kind", "period_start", "trigger", name="uq_reports_period"
        ),
    )

    project_key: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # weekly_status
    trigger: Mapped[str] = mapped_column(String(16))  # manual | scheduled
    period_start: Mapped[date]
    period_end: Mapped[date]
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    content_markdown: Mapped[str] = mapped_column(Text)
    metrics: Mapped[dict[str, Any]]
    generated_by_model: Mapped[str | None] = mapped_column(String(200))
    risk_score: Mapped[int] = mapped_column(default=0)
    risk_level: Mapped[str] = mapped_column(String(16), default="low")
