
import pytest
from pydantic import ValidationError

from backend.app.mcp.schemas.jira import (
    AddCommentInput,
    AssignIssueInput,
    CreateEpicInput,
    CreateStoryInput,
    CreateTaskInput,
    GetIssueCommentsInput,
    GetIssueDependenciesInput,
    GetIssueHistoryInput,
    GetIssueInput,
    GetProjectInput,
    GetProjectIssuesInput,
    GetSprintInput,
    GetSprintIssuesInput,
    LinkIssuesInput,
    SearchIssuesInput,
    TransitionIssueInput,
    UpdateIssueInput,
)


# --------------------------------------------------
# Project input schemas
# --------------------------------------------------

def test_get_project_input_accepts_project_key():
    data = GetProjectInput(project_key="DEMO")

    assert data.project_key == "DEMO"


def test_get_project_input_rejects_empty_key():
    with pytest.raises(ValidationError):
        GetProjectInput(project_key="")


# --------------------------------------------------
# Issue input schemas
# --------------------------------------------------

def test_get_issue_input_accepts_issue_key():
    data = GetIssueInput(issue_key="DEMO-1")

    assert data.issue_key == "DEMO-1"


def test_get_issue_input_rejects_empty_key():
    with pytest.raises(ValidationError):
        GetIssueInput(issue_key="")


# --------------------------------------------------
# Search and pagination schemas
# --------------------------------------------------

def test_search_issues_input_defaults():
    data = SearchIssuesInput(jql="project = DEMO")

    assert data.jql == "project = DEMO"
    assert data.start_at == 0
    assert data.max_results == 50
    assert data.fields is None


@pytest.mark.parametrize("start_at", [-1, -10])
def test_search_issues_rejects_negative_start_at(start_at):
    with pytest.raises(ValidationError):
        SearchIssuesInput(
            jql="project = DEMO",
            start_at=start_at,
        )


@pytest.mark.parametrize("max_results", [0, 101])
def test_search_issues_rejects_invalid_max_results(max_results):
    with pytest.raises(ValidationError):
        SearchIssuesInput(
            jql="project = DEMO",
            max_results=max_results,
        )


def test_search_issues_rejects_empty_jql():
    with pytest.raises(ValidationError):
        SearchIssuesInput(jql="")


def test_get_project_issues_input():
    data = GetProjectIssuesInput(project_key="DEMO")

    assert data.project_key == "DEMO"
    assert data.start_at == 0
    assert data.max_results == 50


def test_get_issue_comments_input():
    data = GetIssueCommentsInput(issue_key="DEMO-1")

    assert data.issue_key == "DEMO-1"
    assert data.start_at == 0
    assert data.max_results == 50


def test_get_issue_history_input():
    data = GetIssueHistoryInput(issue_key="DEMO-1")

    assert data.issue_key == "DEMO-1"
    assert data.start_at == 0
    assert data.max_results == 50


def test_get_issue_dependencies_input():
    data = GetIssueDependenciesInput(issue_key="DEMO-1")

    assert data.issue_key == "DEMO-1"


# --------------------------------------------------
# Sprint input schemas
# --------------------------------------------------

def test_get_sprint_input_accepts_positive_id():
    data = GetSprintInput(sprint_id=42)

    assert data.sprint_id == 42


@pytest.mark.parametrize("sprint_id", [0, -1])
def test_get_sprint_input_rejects_non_positive_id(sprint_id):
    with pytest.raises(ValidationError):
        GetSprintInput(sprint_id=sprint_id)


def test_get_sprint_issues_input_defaults():
    data = GetSprintIssuesInput(sprint_id=42)

    assert data.sprint_id == 42
    assert data.start_at == 0
    assert data.max_results == 50


# --------------------------------------------------
# Create epic, story, and task schemas
# --------------------------------------------------

def test_create_epic_input():
    data = CreateEpicInput(
        project_key="DEMO",
        summary="Platform Epic",
        description="Build the platform",
    )

    assert data.project_key == "DEMO"
    assert data.summary == "Platform Epic"
    assert data.description == "Build the platform"


def test_create_story_input():
    data = CreateStoryInput(
        project_key="DEMO",
        summary="Authentication",
        description="Implement authentication",
        parent_key="DEMO-10",
    )

    assert data.project_key == "DEMO"
    assert data.summary == "Authentication"
    assert data.description == "Implement authentication"
    assert data.parent_key == "DEMO-10"


def test_create_task_input():
    data = CreateTaskInput(
        project_key="DEMO",
        summary="Write tests",
    )

    assert data.project_key == "DEMO"
    assert data.summary == "Write tests"
    assert data.description is None
    assert data.parent_key is None


@pytest.mark.parametrize(
    "schema",
    [CreateEpicInput, CreateStoryInput, CreateTaskInput],
)
def test_create_inputs_reject_empty_project_key(schema):
    with pytest.raises(ValidationError):
        schema(project_key="", summary="A valid summary")


@pytest.mark.parametrize(
    "schema",
    [CreateEpicInput, CreateStoryInput, CreateTaskInput],
)
def test_create_inputs_reject_empty_summary(schema):
    with pytest.raises(ValidationError):
        schema(project_key="DEMO", summary="")


# --------------------------------------------------
# Assign issue input schema
# --------------------------------------------------

def test_assign_issue_input():
    data = AssignIssueInput(
        issue_key="DEMO-1",
        account_id="user-account-123",
    )

    assert data.issue_key == "DEMO-1"
    assert data.account_id == "user-account-123"


def test_assign_issue_input_rejects_empty_values():
    with pytest.raises(ValidationError):
        AssignIssueInput(
            issue_key="",
            account_id="user-account-123",
        )

    with pytest.raises(ValidationError):
        AssignIssueInput(
            issue_key="DEMO-1",
            account_id="",
        )


# --------------------------------------------------
# Update issue input schema
# --------------------------------------------------

def test_update_issue_input_accepts_optional_fields():
    data = UpdateIssueInput(issue_key="DEMO-1")

    assert data.issue_key == "DEMO-1"
    assert data.summary is None
    assert data.description is None
    assert data.priority_id is None
    assert data.assignee_account_id is None


def test_update_issue_input_accepts_fields_to_update():
    data = UpdateIssueInput(
        issue_key="DEMO-1",
        summary="Updated summary",
        description="Updated description",
        priority_id="2",
        assignee_account_id="user-account-123",
    )

    assert data.issue_key == "DEMO-1"
    assert data.summary == "Updated summary"
    assert data.description == "Updated description"
    assert data.priority_id == "2"
    assert data.assignee_account_id == "user-account-123"


def test_update_issue_input_rejects_empty_issue_key():
    with pytest.raises(ValidationError):
        UpdateIssueInput(issue_key="")


def test_update_issue_input_rejects_empty_summary():
    with pytest.raises(ValidationError):
        UpdateIssueInput(
            issue_key="DEMO-1",
            summary="",
        )


# --------------------------------------------------
# Transition issue input schema
# --------------------------------------------------

def test_transition_issue_input():
    data = TransitionIssueInput(
        issue_key="DEMO-1",
        transition_id="31",
    )

    assert data.issue_key == "DEMO-1"
    assert data.transition_id == "31"


def test_transition_issue_input_rejects_empty_values():
    with pytest.raises(ValidationError):
        TransitionIssueInput(
            issue_key="",
            transition_id="31",
        )

    with pytest.raises(ValidationError):
        TransitionIssueInput(
            issue_key="DEMO-1",
            transition_id="",
        )


# --------------------------------------------------
# Add comment input schema
# --------------------------------------------------

def test_add_comment_input():
    data = AddCommentInput(
        issue_key="DEMO-1",
        comment="The fix is ready for review.",
    )

    assert data.issue_key == "DEMO-1"
    assert data.comment == "The fix is ready for review."


def test_add_comment_input_rejects_empty_values():
    with pytest.raises(ValidationError):
        AddCommentInput(
            issue_key="",
            comment="A comment",
        )

    with pytest.raises(ValidationError):
        AddCommentInput(
            issue_key="DEMO-1",
            comment="",
        )


# --------------------------------------------------
# Link issues input schema
# --------------------------------------------------

def test_link_issues_input():
    data = LinkIssuesInput(
        issue_key="DEMO-1",
        linked_issue_key="DEMO-2",
        link_type="Blocks",
    )

    assert data.issue_key == "DEMO-1"
    assert data.linked_issue_key == "DEMO-2"
    assert data.link_type == "Blocks"


def test_link_issues_input_rejects_empty_values():
    with pytest.raises(ValidationError):
        LinkIssuesInput(
            issue_key="",
            linked_issue_key="DEMO-2",
            link_type="Blocks",
        )

    with pytest.raises(ValidationError):
        LinkIssuesInput(
            issue_key="DEMO-1",
            linked_issue_key="",
            link_type="Blocks",
        )

    with pytest.raises(ValidationError):
        LinkIssuesInput(
            issue_key="DEMO-1",
            linked_issue_key="DEMO-2",
            link_type="",
        )
