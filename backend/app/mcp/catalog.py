"""The platform's tool catalog: which tools exist, what they need, what they may do.

Only integrations that are configured are registered, so the planner never sees a
tool that cannot work.
"""

from backend.app.core.config.settings import Settings
from backend.app.core.security.permissions import Permission
from backend.app.mcp.registry.tool_registry import ToolRegistry
from backend.app.mcp.schemas import email as email_schemas
from backend.app.mcp.schemas import jira as jira_schemas
from backend.app.mcp.schemas.common import ToolOperation
from backend.app.mcp.tools.email import EmailTools
from backend.app.mcp.tools.jira import JiraTools

READ, WRITE = ToolOperation.READ, ToolOperation.WRITE


def register_jira_tools(registry: ToolRegistry, tools: JiraTools) -> None:
    read = Permission.PROJECTS_READ
    write = Permission.PROJECTS_WRITE
    s = jira_schemas

    for name, handler, model, description in (
        ("jira.get_project", tools.get_project, s.GetProjectInput,
         "Get a Jira project's details (name, lead, description) by project key."),
        ("jira.get_issue", tools.get_issue, s.GetIssueInput,
         "Get one Jira issue (status, assignee, priority, dates, description) by issue key."),
        ("jira.search_issues", tools.search_issues, s.SearchIssuesInput,
         "Search Jira issues with JQL, e.g. "
         "'project = PHX AND status != Done AND duedate < now()'. "
         "Use `fields` to limit returned fields and `next_page_token` to page."),
        ("jira.get_project_issues", tools.get_project_issues, s.GetProjectIssuesInput,
         "List the most recently created issues of a project."),
        ("jira.get_issue_comments", tools.get_issue_comments, s.GetIssueCommentsInput,
         "Get the comments on a Jira issue."),
        ("jira.get_issue_history", tools.get_issue_history, s.GetIssueHistoryInput,
         "Get an issue's change history (status changes, reassignments, field edits)."),
        ("jira.get_issue_dependencies", tools.get_issue_dependencies, s.GetIssueDependenciesInput,
         "Get the issues linked to an issue (blocks / is blocked by / relates to)."),
        ("jira.get_sprint", tools.get_sprint, s.GetSprintInput,
         "Get a sprint's details (name, state, start/end dates, goal) by numeric sprint id."),
        ("jira.get_sprint_issues", tools.get_sprint_issues, s.GetSprintIssuesInput,
         "List the issues in a sprint by numeric sprint id."),
    ):  # fmt: skip
        registry.register(
            name, handler, description=description, input_model=model,
            operation=READ, permission=read,
        )  # fmt: skip

    for name, handler, model, description in (
        ("jira.create_epic", tools.create_epic, s.CreateEpicInput,
         "Create a Jira epic in a project."),
        ("jira.create_story", tools.create_story, s.CreateStoryInput,
         "Create a Jira story, optionally under a parent epic."),
        ("jira.create_task", tools.create_task, s.CreateTaskInput,
         "Create a Jira task, optionally under a parent issue."),
        ("jira.assign_issue", tools.assign_issue, s.AssignIssueInput,
         "Assign a Jira issue to a user by Atlassian account id."),
        ("jira.update_issue", tools.update_issue, s.UpdateIssueInput,
         "Update an issue's summary, description, priority or assignee."),
        ("jira.transition_issue", tools.transition_issue, s.TransitionIssueInput,
         "Move an issue to another workflow state using a transition id."),
        ("jira.add_comment", tools.add_comment, s.AddCommentInput,
         "Add a comment to a Jira issue."),
        ("jira.link_issues", tools.link_issues, s.LinkIssuesInput,
         "Link two Jira issues with a link type such as 'Blocks' or 'Relates'."),
    ):  # fmt: skip
        registry.register(
            name, handler, description=description, input_model=model,
            operation=WRITE, permission=write,
        )  # fmt: skip


def register_email_tools(registry: ToolRegistry, tools: EmailTools) -> None:
    read = Permission.EMAIL_READ
    registry.register(
        "gmail.search_emails",
        tools.search_emails,
        description=(
            "Search the connected mailbox with Gmail search syntax, e.g. "
            "'from:client@acme.com newer_than:7d subject:release'. Returns message ids."
        ),
        input_model=email_schemas.SearchEmailsInput,
        operation=READ,
        permission=read,
    )
    registry.register(
        "gmail.get_email",
        tools.get_email,
        description="Read one email (snippet and body) by message id from gmail.search_emails.",
        input_model=email_schemas.GetEmailInput,
        operation=READ,
        permission=read,
    )


def build_tool_registry(
    settings: Settings,
    *,
    jira_tools: JiraTools | None = None,
    email_tools: EmailTools | None = None,
) -> ToolRegistry:
    registry = ToolRegistry(
        default_timeout=settings.tool_timeout_seconds,
        read_retries=settings.tool_read_retries,
        retry_backoff=settings.tool_retry_backoff_seconds,
    )

    if jira_tools is not None or settings.jira_configured:
        register_jira_tools(registry, jira_tools or JiraTools())
    if email_tools is not None or settings.gmail_configured:
        register_email_tools(registry, email_tools or EmailTools())

    return registry
