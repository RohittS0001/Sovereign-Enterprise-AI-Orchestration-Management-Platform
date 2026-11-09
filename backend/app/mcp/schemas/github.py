from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Repository = Field(description="Repository as 'owner/name', e.g. 'acme/payments'")


class ListPullRequestsInput(BaseModel):
    repository: str = Repository
    state: Literal["open", "closed", "all"] = "open"
    base: str | None = Field(default=None, description="Only PRs targeting this branch")
    limit: int = Field(default=20, ge=1, le=50)


class GetPullRequestInput(BaseModel):
    repository: str = Repository
    number: int = Field(gt=0)


class SearchPullRequestsInput(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=256,
        description="GitHub search terms, e.g. 'PHX-7 is:open' or 'review:required author:priya'",
    )
    limit: int = Field(default=20, ge=1, le=50)


class ListCommitsInput(BaseModel):
    repository: str = Repository
    branch: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    author: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=30, ge=1, le=100)


class ListWorkflowRunsInput(BaseModel):
    repository: str = Repository
    branch: str | None = None
    status: (
        Literal["completed", "in_progress", "queued", "success", "failure", "cancelled"] | None
    ) = None
    limit: int = Field(default=20, ge=1, le=50)


class GetCommitChecksInput(BaseModel):
    repository: str = Repository
    ref: str = Field(min_length=1, description="Branch name or commit SHA")


class AddPullRequestCommentInput(BaseModel):
    repository: str = Repository
    number: int = Field(gt=0)
    body: str = Field(min_length=1, max_length=10000)
