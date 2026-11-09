from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class SearchEmployeesInput(BaseModel):
    search: str | None = Field(
        default=None, min_length=3, max_length=100, description="Name, email or employee number"
    )
    employment_status: Literal["Working", "Relieved", "All"] = "Working"
    limit: int = Field(default=50, ge=1, le=200)


class GetEmployeeInput(BaseModel):
    employee_id: str = Field(min_length=1, max_length=64)


class DateRangeInput(BaseModel):
    from_date: date
    to_date: date
    employee_ids: list[str] | None = Field(default=None, max_length=100)
    limit: int = Field(default=100, ge=1, le=200)


class GetLeaveBalancesInput(BaseModel):
    employee_ids: list[str] | None = Field(default=None, max_length=100)
    limit: int = Field(default=100, ge=1, le=200)
