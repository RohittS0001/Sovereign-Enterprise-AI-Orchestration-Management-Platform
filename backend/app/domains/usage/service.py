"""Agent usage accounting (tokens, runs, tool calls) and per-user daily quotas."""

import uuid
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.errors import AppException
from backend.app.db.base import as_utc
from backend.app.db.models import AgentRun, RunStatus, User


class QuotaExceededError(AppException):
    status_code = 429
    code = "quota_exceeded"


class UserUsage(BaseModel):
    user_id: uuid.UUID
    email: str | None
    runs: int
    failed_runs: int
    tool_calls: int
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class DailyUsage(BaseModel):
    day: str
    runs: int
    tokens: int


class UsageSummary(BaseModel):
    since: datetime
    runs: int
    tokens: int
    by_user: list[UserUsage]
    by_day: list[DailyUsage]


def _start_of_today() -> datetime:
    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def tokens_used_today(session: AsyncSession, user_id: uuid.UUID) -> int:
    total = await session.scalar(
        select(
            func.coalesce(func.sum(AgentRun.prompt_tokens + AgentRun.completion_tokens), 0)
        ).where(AgentRun.user_id == user_id, AgentRun.started_at >= _start_of_today())
    )
    return int(total or 0)


async def enforce_daily_quota(session: AsyncSession, user_id: uuid.UUID, quota: int) -> None:
    if quota <= 0:
        return
    used = await tokens_used_today(session, user_id)
    if used >= quota:
        raise QuotaExceededError(
            "Daily AI token quota reached; it resets at 00:00 UTC",
            details={"used_tokens": used, "quota": quota},
        )


async def usage_summary(
    session: AsyncSession, *, days: int, user_id: uuid.UUID | None = None
) -> UsageSummary:
    since = datetime.now(UTC) - timedelta(days=days)
    conditions = [AgentRun.started_at >= since]
    if user_id is not None:
        conditions.append(AgentRun.user_id == user_id)

    rows = (
        await session.execute(
            select(AgentRun, User.email)
            .join(User, User.id == AgentRun.user_id, isouter=True)
            .where(*conditions)
        )
    ).all()

    per_user: dict[uuid.UUID, UserUsage] = {}
    per_day: dict[str, DailyUsage] = {}
    for run, email in rows:
        usage = per_user.setdefault(
            run.user_id,
            UserUsage(user_id=run.user_id, email=email, runs=0, failed_runs=0, tool_calls=0,
                      prompt_tokens=0, completion_tokens=0),
        )  # fmt: skip
        usage.runs += 1
        usage.failed_runs += run.status == RunStatus.FAILED
        usage.tool_calls += len(run.steps or [])
        usage.prompt_tokens += run.prompt_tokens
        usage.completion_tokens += run.completion_tokens

        day = as_utc(run.started_at).date().isoformat()
        daily = per_day.setdefault(day, DailyUsage(day=day, runs=0, tokens=0))
        daily.runs += 1
        daily.tokens += run.prompt_tokens + run.completion_tokens

    by_user = sorted(per_user.values(), key=lambda u: -u.total_tokens)
    return UsageSummary(
        since=since,
        runs=sum(u.runs for u in by_user),
        tokens=sum(u.total_tokens for u in by_user),
        by_user=by_user,
        by_day=sorted(per_day.values(), key=lambda d: d.day),
    )
