from typing import Annotated

from fastapi import APIRouter, Query

from backend.app.api.deps import PrincipalDep, SessionDep, SettingsDep
from backend.app.core.security.permissions import Permission
from backend.app.services.usage_service import UsageSummary, tokens_used_today, usage_summary

router = APIRouter(prefix="/api/v1/usage", tags=["Usage"])


@router.get("", response_model=UsageSummary)
async def usage(
    principal: PrincipalDep,
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> UsageSummary:
    """Agent usage. Admins see everyone; other users see only their own."""
    scope = None if principal.has(Permission.USERS_MANAGE) else principal.user_id
    return await usage_summary(session, days=days, user_id=scope)


@router.get("/me/today")
async def my_quota(principal: PrincipalDep, session: SessionDep, settings: SettingsDep) -> dict:
    used = await tokens_used_today(session, principal.user_id)
    quota = settings.agent_daily_token_quota
    return {
        "used_tokens": used,
        "quota": quota or None,
        "remaining": max(0, quota - used) if quota else None,
    }
