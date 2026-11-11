"""In-process weekly report scheduler.

Every few minutes it checks whether the configured weekday/hour (UTC) has passed this
week and generates any missing scheduled reports. With several workers/replicas only one
does the work: on PostgreSQL each run first takes a session-level advisory lock
(``pg_try_advisory_lock``); the others skip that tick instead of collecting data and calling
the LLM for nothing. The lock is released when the run ends (or its connection drops, so a
crashed leader never blocks the rest). The unique constraint on reports still guarantees
one report per project and week even if two runs ever overlapped.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime

from sqlalchemy import select, text

from backend.app.core.config import Settings
from backend.app.core.logging import get_logger
from backend.app.db.models import Project
from backend.app.db.session import Database
from backend.app.domains.analytics.collector import SnapshotCollector
from backend.app.domains.reporting.generator import generate_scheduled
from backend.app.llm.base import AgentModels

logger = get_logger(__name__)

CHECK_INTERVAL_SECONDS = 300
LEADER_LOCK_ID = 0x5E7E_2E90  # any constant shared by every replica of this application


@contextlib.asynccontextmanager
async def leader_lock(database: Database) -> AsyncIterator[bool]:
    """Yield True when this process may run the scheduled job now.

    PostgreSQL: a non-blocking advisory lock on a dedicated connection. Other backends
    (SQLite: one process) are always the leader.
    """
    if database.backend != "postgresql":
        yield True
        return
    async with database.engine.connect() as connection:
        acquired = bool(
            await connection.scalar(
                text("SELECT pg_try_advisory_lock(:id)"), {"id": LEADER_LOCK_ID}
            )
        )
        try:
            yield acquired
        finally:
            if acquired:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:id)"), {"id": LEADER_LOCK_ID}
                )


class ReportScheduler:
    def __init__(
        self,
        database: Database,
        collector: SnapshotCollector,
        settings: Settings,
        models: AgentModels | None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.database = database
        self.collector = collector
        self.settings = settings
        self.models = models
        self.clock = clock
        self._task: asyncio.Task | None = None

    def is_due(self, now: datetime) -> bool:
        weekday, hour = (
            self.settings.report_schedule_weekday,
            self.settings.report_schedule_hour_utc,
        )
        return (now.weekday(), now.hour) >= (weekday, hour)

    async def run_due(self) -> list[str]:
        """Generate this week's missing reports if the schedule has passed. Returns keys."""
        now = self.clock()
        if not self.is_due(now):
            return []
        async with leader_lock(self.database) as leader:
            if not leader:
                logger.info("Another replica is generating scheduled reports; skipping")
                return []
            return await self._generate(now)

    async def _generate(self, now: datetime) -> list[str]:
        created = []
        async with self.database.session() as session:
            keys = list(
                await session.scalars(select(Project.key).where(Project.is_active.is_(True)))
            )
            for key in keys:
                try:
                    report = await generate_scheduled(
                        session,
                        self.collector,
                        key,
                        model=self.models.reporting if self.models else None,
                        today=now.date(),
                    )
                except Exception:
                    logger.exception("Scheduled report failed", extra={"project": key})
                    await session.rollback()
                    continue
                if report is not None:
                    created.append(key)
        if created:
            logger.info("Scheduled reports generated", extra={"projects": created})
        return created

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_due()
            except Exception:
                logger.exception("Report scheduler iteration failed")
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="report-scheduler")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
