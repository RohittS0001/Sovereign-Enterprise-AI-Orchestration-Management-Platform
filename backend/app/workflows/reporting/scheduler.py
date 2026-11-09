"""In-process weekly report scheduler.

Every few minutes it checks whether the configured weekday/hour (UTC) has passed this
week and generates any missing scheduled reports. Running several API workers is safe:
a unique constraint lets only one of them create each report.
"""

import asyncio
import contextlib
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select

from backend.app.analytics.collector import SnapshotCollector
from backend.app.core.config.settings import Settings
from backend.app.core.logging.logger import get_logger
from backend.app.db.models import Project
from backend.app.db.session import Database
from backend.app.llm.base import AgentModels
from backend.app.workflows.reporting.generator import generate_scheduled

logger = get_logger(__name__)

CHECK_INTERVAL_SECONDS = 300


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
