from pydantic import BaseModel, Field


class CreateEpicRequest(BaseModel):
    project_key: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    description: str | None = None


class CreateStoryRequest(BaseModel):
    project_key: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    description: str | None = None
    parent_key: str | None = None


class CreateTaskRequest(BaseModel):
    project_key: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    description: str | None = None
    parent_key: str | None = None


class AssignIssueRequest(BaseModel):
    issue_key: str = Field(min_length=1)
    account_id: str = Field(min_length=1)


class UpdateIssueRequest(BaseModel):
    issue_key: str = Field(min_length=1)
    summary: str | None = None
    description: str | None = None
    priority_id: str | None = None
    assignee_account_id: str | None = None


class TransitionIssueRequest(BaseModel):
    issue_key: str = Field(min_length=1)
    transition_id: str = Field(min_length=1)


class AddCommentRequest(BaseModel):
    issue_key: str = Field(min_length=1)
    comment: str = Field(min_length=1)


class LinkIssuesRequest(BaseModel):
    issue_key: str = Field(min_length=1)
    linked_issue_key: str = Field(min_length=1)
    link_type: str = Field(min_length=1)