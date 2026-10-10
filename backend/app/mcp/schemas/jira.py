
from pydantic import BaseModel, Field


class GetProjectInput(BaseModel):
    project_key: str = Field(min_length=1)


class ProjectData(BaseModel):
    project_id: str
    name: str
    key: str
    description: str | None = None


class GetProjectOutput(BaseModel):
    project: ProjectData


class GetIssueInput(BaseModel):
    issue_key: str = Field(min_length=1)


class SearchIssuesInput(BaseModel):
    jql: str = Field(min_length=1)
    start_at: int = Field(default=0, ge=0)
    max_results: int = Field(default=50, ge=1, le=100)
    fields: list[str] | None = None


class GetProjectIssuesInput(BaseModel):
    project_key: str = Field(min_length=1)
    start_at: int = Field(default=0, ge=0)
    max_results: int = Field(default=50, ge=1, le=100)


class GetIssueCommentsInput(BaseModel):
    issue_key: str = Field(min_length=1)
    start_at: int = Field(default=0, ge=0)
    max_results: int = Field(default=50, ge=1, le=100)


class GetIssueHistoryInput(BaseModel):
    issue_key: str = Field(min_length=1)
    start_at: int = Field(default=0, ge=0)
    max_results: int = Field(default=50, ge=1, le=100)


class GetIssueDependenciesInput(BaseModel):
    issue_key: str = Field(min_length=1)


class GetSprintInput(BaseModel):
    sprint_id: int = Field(gt=0)


class GetSprintIssuesInput(BaseModel):
    sprint_id: int = Field(gt=0)
    start_at: int = Field(default=0, ge=0)
    max_results: int = Field(default=50, ge=1, le=100)



class CreateEpicInput(BaseModel):
    project_key: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    description: str | None = None


class CreateStoryInput(BaseModel):
    project_key: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    description: str | None = None
    parent_key: str | None = None


class CreateTaskInput(BaseModel):
    project_key: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    description: str | None = None
    parent_key: str | None = None


class AssignIssueInput(BaseModel):
    issue_key: str = Field(min_length=1)
    account_id: str = Field(min_length=1)


class UpdateIssueInput(BaseModel):
    issue_key: str = Field(min_length=1)
    summary: str | None = Field(default=None, min_length=1)
    description: str | None = None
    priority_id: str | None = Field(default=None, min_length=1)
    assignee_account_id: str | None = Field(default=None, min_length=1)


class TransitionIssueInput(BaseModel):
    issue_key: str = Field(min_length=1)
    transition_id: str = Field(min_length=1)


class AddCommentInput(BaseModel):
    issue_key: str = Field(min_length=1)
    comment: str = Field(min_length=1)


class LinkIssuesInput(BaseModel):
    issue_key: str = Field(min_length=1)
    linked_issue_key: str = Field(min_length=1)
    link_type: str = Field(min_length=1)
