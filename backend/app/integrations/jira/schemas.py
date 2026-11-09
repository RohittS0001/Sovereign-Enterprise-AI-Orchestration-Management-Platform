from pydantic import BaseModel, Field

from backend.app.integrations.jira.validators import IssueKey, ProjectKey


class CreateEpicRequest(BaseModel):
    project_key: ProjectKey
    summary: str = Field(min_length=1)
    description: str | None = None


class CreateStoryRequest(BaseModel):
    project_key: ProjectKey
    summary: str = Field(min_length=1)
    description: str | None = None
    parent_key: IssueKey | None = None


class CreateTaskRequest(BaseModel):
    project_key: ProjectKey
    summary: str = Field(min_length=1)
    description: str | None = None
    parent_key: IssueKey | None = None


class AssignIssueRequest(BaseModel):
    issue_key: IssueKey
    account_id: str = Field(min_length=1)


class UpdateIssueRequest(BaseModel):
    issue_key: IssueKey
    summary: str | None = None
    description: str | None = None
    priority_id: str | None = None
    assignee_account_id: str | None = None


class TransitionIssueRequest(BaseModel):
    issue_key: IssueKey
    transition_id: str = Field(min_length=1)


class AddCommentRequest(BaseModel):
    issue_key: IssueKey
    comment: str = Field(min_length=1)


class LinkIssuesRequest(BaseModel):
    issue_key: IssueKey
    linked_issue_key: IssueKey
    link_type: str = Field(min_length=1)
