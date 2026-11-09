import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base

# BIGINT on PostgreSQL, INTEGER on SQLite (SQLite only treats INTEGER keys specially).
SeqType = BigInteger().with_variant(Integer(), "sqlite")


class AuditEvent(Base):
    """An append-only, hash-chained record of a security-relevant action.

    ``hash`` covers this event's fields plus the previous event's hash, so editing or
    deleting any past event breaks verification from that point onward.
    """

    __tablename__ = "audit_events"

    seq: Mapped[int] = mapped_column(SeqType, primary_key=True, autoincrement=False)
    occurred_at: Mapped[datetime] = mapped_column(index=True)
    actor_type: Mapped[str] = mapped_column(String(16))  # human | agent | system
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))
    request_id: Mapped[str | None] = mapped_column(String(128))
    run_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    details: Mapped[dict[str, Any]]
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class AuditChainHead(Base):
    """Single row holding the latest sequence number and hash.

    Writers lock this row (``SELECT ... FOR UPDATE``) so concurrent events are chained
    one after another without gaps or forks.
    """

    __tablename__ = "audit_chain_head"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_seq: Mapped[int] = mapped_column(SeqType)
    last_hash: Mapped[str] = mapped_column(String(64))
