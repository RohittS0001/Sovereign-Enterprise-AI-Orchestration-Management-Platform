"""Result models for analytics (returned by the API, the agent tools and reports)."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class SprintForecast(BaseModel):
    sprint: str
    start: date | None
    end: date | None
    working_days_total: int
    working_days_elapsed: int
    working_days_left: int
    committed_points: float
    done_points: float
    burn_rate_per_day: float
    projected_points: float
    completion_ratio: float
    status: Literal["on_track", "at_risk", "off_track", "unknown"]
    remaining_issues: list[str]
    note: str | None = None


class MemberLoad(BaseModel):
    name: str
    open_issues: list[str]
    open_points: float
    capacity_points: float
    availability: float = Field(description="Share of the remaining period not on leave (0-1)")
    effective_capacity: float
    load_ratio: float
    status: Literal["overloaded", "balanced", "underused", "unknown"]
    leave_days: int
    at_risk_issues: list[str] = Field(
        default_factory=list, description="Open issues due while the assignee is on leave"
    )


class Reassignment(BaseModel):
    issue: str
    points: float
    from_member: str
    to_member: str
    reason: str


class Workload(BaseModel):
    period_end: date
    members: list[MemberLoad]
    unassigned_open: list[str]
    suggestions: list[Reassignment]


class Contradiction(BaseModel):
    kind: Literal["done_but_pr_open", "review_without_pr", "merged_but_not_done"]
    issue: str
    detail: str
    evidence: list[str]


class RiskFactor(BaseModel):
    name: str
    score: float
    max_score: float
    evidence: list[str]
    recommendation: str | None = None


class RiskAssessment(BaseModel):
    score: int
    level: Literal["low", "medium", "high"]
    factors: list[RiskFactor]


class ProductionHealth(BaseModel):
    incidents: list[dict] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)
    deployments: list[dict] = Field(default_factory=list)
    quality_gates: list[dict] = Field(default_factory=list)


class ProjectHealth(BaseModel):
    project: str
    name: str
    as_of: date
    risk: RiskAssessment
    forecast: SprintForecast | None
    workload: Workload
    contradictions: list[Contradiction]
    issue_counts: dict[str, int]
    warnings: list[str]
    production: ProductionHealth | None = None
    finance: dict | None = None  # earned value (analytics/evm.py)
