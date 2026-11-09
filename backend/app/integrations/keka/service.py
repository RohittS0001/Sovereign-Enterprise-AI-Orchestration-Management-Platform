"""People, leave and attendance data from Keka, for capacity planning.

Employee records are reduced to work-related fields (data minimization): personal
details such as date of birth, phone, address or bank data never leave this module.
"""

import re
from collections.abc import Callable
from datetime import date
from typing import Any

from backend.app.integrations.keka.client import KekaClient

MAX_RANGE_DAYS = 90  # Keka rejects longer date ranges
_EMPLOYEE_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def validate_employee_ids(ids: list[str] | None) -> str | None:
    if not ids:
        return None
    for value in ids:
        if not _EMPLOYEE_ID.match(value):
            raise ValueError(f"Invalid Keka employee id {value!r}")
    return ",".join(ids)


def validate_range(start: date, end: date) -> None:
    if start > end:
        raise ValueError("from_date must be on or before to_date")
    if (end - start).days > MAX_RANGE_DAYS:
        raise ValueError(f"Date range cannot exceed {MAX_RANGE_DAYS} days")


def _lookup(value: Any, *keys: str) -> Any:
    if isinstance(value, dict):
        for key in keys:
            if value.get(key):
                return value[key]
    return None


def summarize_employee(e: dict[str, Any]) -> dict[str, Any]:
    reports_to = e.get("reportsTo") or {}
    return {
        "id": e.get("id"),
        "employee_number": e.get("employeeNumber"),
        "name": e.get("displayName"),
        "email": e.get("email"),
        "job_title": _lookup(e.get("jobTitle"), "title", "name"),
        "department": _lookup(e.get("department"), "title", "name"),
        "location": _lookup(e.get("location"), "title", "name"),
        "reports_to": {"id": reports_to.get("id"), "name": reports_to.get("displayName")}
        if reports_to
        else None,
        "joining_date": e.get("joiningDate"),
        "employment_status_code": e.get("employmentStatus"),
    }


def summarize_leave(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "employee_id": item.get("employeeIdentifier"),
        "employee_number": item.get("employeeNumber"),
        "from": item.get("fromDate"),
        "to": item.get("toDate"),
        "from_session": item.get("fromSession"),
        "to_session": item.get("toSession"),
        "status_code": item.get("status"),
        "leave_types": [
            {"type": sel.get("leaveTypeName"), "count": sel.get("count")}
            for sel in item.get("selection") or []
        ],
        "requested_on": item.get("requestedOn"),
    }


def _page(body: Any) -> tuple[list[dict[str, Any]], int]:
    if isinstance(body, dict):
        data = body.get("data") or []
        return data, body.get("totalRecords", len(data))
    return list(body or []), len(body or [])


class KekaService:
    def __init__(
        self,
        client: KekaClient | None = None,
        *,
        client_factory: Callable[[], KekaClient] = KekaClient,
    ) -> None:
        self._client = client
        self._client_factory = client_factory

    @property
    def client(self) -> KekaClient:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()

    async def health_check(self) -> dict:
        return await self.client.health_check()

    async def search_employees(
        self, search: str | None = None, *, employment_status: str = "Working", limit: int = 50
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"pageSize": limit, "pageNumber": 1}
        if search:
            params["searchKey"] = search
        if employment_status != "All":
            params["employmentStatus"] = employment_status
        data, total = _page(await self.client.get("/hris/employees", params=params))
        return {"total": total, "employees": [summarize_employee(e) for e in data]}

    async def get_employee(self, employee_id: str) -> dict[str, Any]:
        validate_employee_ids([employee_id])
        body = await self.client.get(f"/hris/employees/{employee_id}")
        record = body.get("data", body) if isinstance(body, dict) else body
        return summarize_employee(record or {})

    async def get_leave_requests(
        self,
        from_date: date,
        to_date: date,
        employee_ids: list[str] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        validate_range(from_date, to_date)
        params: dict[str, Any] = {
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
            "pageSize": limit,
            "pageNumber": 1,
        }
        if ids := validate_employee_ids(employee_ids):
            params["employeeIds"] = ids
        data, total = _page(await self.client.get("/time/leaverequests", params=params))
        return {"total": total, "leave_requests": [summarize_leave(i) for i in data]}

    async def get_leave_balances(
        self, employee_ids: list[str] | None = None, limit: int = 100
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"pageSize": limit, "pageNumber": 1}
        if ids := validate_employee_ids(employee_ids):
            params["employeeIds"] = ids
        data, total = _page(await self.client.get("/time/leavebalance", params=params))
        return {"total": total, "leave_balances": data}

    async def get_attendance(
        self,
        from_date: date,
        to_date: date,
        employee_ids: list[str] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        validate_range(from_date, to_date)
        params: dict[str, Any] = {
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
            "pageSize": limit,
            "pageNumber": 1,
        }
        if ids := validate_employee_ids(employee_ids):
            params["employeeIds"] = ids
        data, total = _page(await self.client.get("/time/attendance", params=params))
        return {"total": total, "attendance": data}
