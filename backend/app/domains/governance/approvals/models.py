import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    REJECTED = "rejected"
    EXPIRED = "expired"
    APPROVED = "approved"  # claimed by an approver; the action is running (or its outcome
    # is unknown after a crash: it is never re-run automatically, an external write may
    # already have happened)
    EXECUTED = "executed"  # approved and the action succeeded
    FAILED = "failed"  # approved but the action failed


class ApprovalRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A write action proposed by the agent, waiting for a human decision."""

    __tablename__ = "approval_requests"

    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True
    )
    requested_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    tool_name: Mapped[str] = mapped_column(String(100))
    permission: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[dict[str, Any]]
    summary: Mapped[str] = mapped_column(Text)
    risk_flags: Mapped[list[str]]
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(
            ApprovalStatus,
            native_enum=False,
            length=16,
            values_callable=lambda items: [i.value for i in items],
            validate_strings=True,
        ),
        default=ApprovalStatus.PENDING,
        index=True,
    )
    expires_at: Mapped[datetime]
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None]
    decision_note: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None]
    error: Mapped[str | None] = mapped_column(Text)
