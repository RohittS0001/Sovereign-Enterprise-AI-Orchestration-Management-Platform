from typing import Any

from backend.app.integrations.keka.service import KekaService
from backend.app.tools.base import ToolResult
from backend.app.tools.keka import schemas as s
from backend.app.tools.runner import run_tool


class KekaTools:
    """MCP-facing tools for Keka (people, leave, attendance)."""

    def __init__(self, service: KekaService | None = None) -> None:
        self.service = service or KekaService()

    async def search_employees(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "Keka", "search employees", payload, s.SearchEmployeesInput,
            lambda i: self.service.search_employees(
                i.search, employment_status=i.employment_status, limit=i.limit
            ),
        )  # fmt: skip

    async def get_employee(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "Keka", "get employee", payload, s.GetEmployeeInput,
            lambda i: self.service.get_employee(i.employee_id),
        )  # fmt: skip

    async def get_leave_requests(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "Keka", "get leave requests", payload, s.DateRangeInput,
            lambda i: self.service.get_leave_requests(
                i.from_date, i.to_date, i.employee_ids, limit=i.limit
            ),
        )  # fmt: skip

    async def get_leave_balances(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "Keka", "get leave balances", payload, s.GetLeaveBalancesInput,
            lambda i: self.service.get_leave_balances(i.employee_ids, limit=i.limit),
        )  # fmt: skip

    async def get_attendance(self, payload: dict[str, Any]) -> ToolResult:
        return await run_tool(
            "Keka", "get attendance", payload, s.DateRangeInput,
            lambda i: self.service.get_attendance(
                i.from_date, i.to_date, i.employee_ids, limit=i.limit
            ),
        )  # fmt: skip
