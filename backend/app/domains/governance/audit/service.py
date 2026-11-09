"""Tamper-evident audit log.

Events are written in the caller's transaction (so a rolled-back action leaves no
event, and the caller commits once). Writers lock the chain head row, which serializes
event numbering in PostgreSQL; keep the time between ``record`` and ``commit`` short.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.context import get_request_id
from backend.app.core.security.permissions import Principal
from backend.app.db.base import as_utc, utcnow
from backend.app.db.models import AuditChainHead, AuditEvent
from backend.app.domains.governance.guardrails.redaction import redact_data

GENESIS_HASH = "0" * 64
_HEAD_ID = 1


def _timestamp(value: datetime) -> str:
    return as_utc(value).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def compute_hash(
    *,
    seq: int,
    prev_hash: str,
    occurred_at: datetime,
    actor_type: str,
    actor_user_id: uuid.UUID | None,
    action: str,
    resource_type: str | None,
    resource_id: str | None,
    status: str,
    request_id: str | None,
    run_id: uuid.UUID | None,
    details: dict[str, Any],
) -> str:
    canonical = json.dumps(
        {
            "seq": seq,
            "prev_hash": prev_hash,
            "occurred_at": _timestamp(occurred_at),
            "actor_type": actor_type,
            "actor_user_id": str(actor_user_id) if actor_user_id else None,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "status": status,
            "request_id": request_id,
            "run_id": str(run_id) if run_id else None,
            "details": details,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _event_hash(event: AuditEvent) -> str:
    return compute_hash(
        seq=event.seq,
        prev_hash=event.prev_hash,
        occurred_at=event.occurred_at,
        actor_type=event.actor_type,
        actor_user_id=event.actor_user_id,
        action=event.action,
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        status=event.status,
        request_id=event.request_id,
        run_id=event.run_id,
        details=event.details,
    )


@dataclass
class ChainVerification:
    valid: bool
    checked: int
    first_invalid_seq: int | None = None
    reason: str | None = None


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        action: str,
        *,
        status: str,
        principal: Principal | None = None,
        actor_type: str | None = None,
        actor_user_id: uuid.UUID | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        run_id: uuid.UUID | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Append an event (caller commits). Details are redacted and JSON-normalized."""
        head = await self._locked_head()

        clean_details = json.loads(json.dumps(redact_data(details or {}), default=str))
        event = AuditEvent(
            seq=head.last_seq + 1,
            occurred_at=utcnow(),
            actor_type=actor_type or (principal.actor_type if principal else "system"),
            actor_user_id=actor_user_id or (principal.user_id if principal else None),
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            status=status,
            request_id=get_request_id(),
            run_id=run_id,
            details=clean_details,
            prev_hash=head.last_hash,
            hash="",
        )
        event.hash = _event_hash(event)

        head.last_seq = event.seq
        head.last_hash = event.hash
        self.session.add(event)
        await self.session.flush()
        return event

    async def _locked_head(self) -> AuditChainHead:
        head = await self.session.scalar(
            select(AuditChainHead).where(AuditChainHead.id == _HEAD_ID).with_for_update()
        )
        if head is None:
            head = AuditChainHead(id=_HEAD_ID, last_seq=0, last_hash=GENESIS_HASH)
            self.session.add(head)
            await self.session.flush()
        return head

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        action: str | None = None,
        actor_user_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
    ) -> tuple[list[AuditEvent], int]:
        filters = []
        if action:
            filters.append(AuditEvent.action == action)
        if actor_user_id:
            filters.append(AuditEvent.actor_user_id == actor_user_id)
        if run_id:
            filters.append(AuditEvent.run_id == run_id)

        total = await self.session.scalar(
            select(func.count()).select_from(AuditEvent).where(*filters)
        )
        events = await self.session.scalars(
            select(AuditEvent)
            .where(*filters)
            .order_by(AuditEvent.seq.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(events), total or 0

    async def verify_chain(self, batch_size: int = 1000) -> ChainVerification:
        """Recompute every hash in order and check the links and the head."""
        expected_prev = GENESIS_HASH
        expected_seq = 1
        checked = 0

        while True:
            batch = list(
                await self.session.scalars(
                    select(AuditEvent)
                    .where(AuditEvent.seq >= expected_seq)
                    .order_by(AuditEvent.seq)
                    .limit(batch_size)
                )
            )
            if not batch:
                break

            for event in batch:
                if event.seq != expected_seq:
                    return ChainVerification(False, checked, expected_seq, "missing event")
                if event.prev_hash != expected_prev:
                    return ChainVerification(False, checked, event.seq, "broken link")
                if _event_hash(event) != event.hash:
                    return ChainVerification(False, checked, event.seq, "content modified")
                expected_prev = event.hash
                expected_seq += 1
                checked += 1

        head = await self.session.get(AuditChainHead, _HEAD_ID)
        if head is not None and (head.last_seq != checked or head.last_hash != expected_prev):
            # Events after the last one we found were deleted (tail truncation).
            return ChainVerification(False, checked, checked + 1, "events missing at end")

        return ChainVerification(True, checked)
