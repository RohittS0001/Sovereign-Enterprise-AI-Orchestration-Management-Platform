import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, UUIDPrimaryKeyMixin, utcnow


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    AWAITING_APPROVAL = "awaiting_approval"
    FAILED = "failed"


class AgentRun(UUIDPrimaryKeyMixin, Base):
    """One question to the agent: its plan, tool steps, answer and token usage."""

    __tablename__ = "agent_runs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    query: Mapped[str] = mapped_column(Text)
    status: Mapped[RunStatus] = mapped_column(
        Enum(
            RunStatus,
            native_enum=False,
            length=24,
            values_callable=lambda items: [i.value for i in items],
            validate_strings=True,
        ),
        default=RunStatus.RUNNING,
        index=True,
    )
    answer: Mapped[str | None] = mapped_column(Text)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(default=list)
    citations: Mapped[list[str]] = mapped_column(default=list)
    flagged_injection: Mapped[bool] = mapped_column(default=False)
    error: Mapped[str | None] = mapped_column(Text)
    operations_model: Mapped[str | None] = mapped_column(String(200))
    reporting_model: Mapped[str | None] = mapped_column(String(200))
    prompt_tokens: Mapped[int] = mapped_column(default=0)
    completion_tokens: Mapped[int] = mapped_column(default=0)
    request_id: Mapped[str | None] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    finished_at: Mapped[datetime | None]
