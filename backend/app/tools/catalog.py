"""The platform's tool catalog: which tools exist, what they need, what they may do.

Only integrations that are configured are registered, so the planner never sees a
tool that cannot work.
"""

from backend.app.core.config import Settings
from backend.app.core.security.permissions import Permission
from backend.app.integrations.hub import IntegrationHub, build_integration_hub
from backend.app.tools.analytics import AnalyticsTools, ListProjectsInput, ProjectHealthInput
from backend.app.tools.base import ToolOperation
from backend.app.tools.github import schemas as github_schemas
from backend.app.tools.github.tools import GitHubTools
from backend.app.tools.gmail import schemas as email_schemas
from backend.app.tools.gmail.tools import EmailTools
from backend.app.tools.jira import schemas as jira_schemas
from backend.app.tools.jira.tools import JiraTools
from backend.app.tools.keka import schemas as keka_schemas
from backend.app.tools.keka.tools import KekaTools
from backend.app.tools.knowledge import KnowledgeTools, SearchKnowledgeInput
from backend.app.tools.registry import ToolRegistry

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
    registry.register(
        "gmail.create_draft",
        tools.create_draft,
        description=(
            "Draft (never send) an email in the connected mailbox for a person to review and "
            "send from Gmail. Use only when the user asks for an email to be prepared."
        ),
        input_model=email_schemas.CreateDraftInput,
        operation=WRITE,
        permission=Permission.EMAIL_SEND,
    )


def register_github_tools(registry: ToolRegistry, tools: GitHubTools) -> None:
    s = github_schemas
    for name, handler, model, description in (
        ("github.list_pull_requests", tools.list_pull_requests, s.ListPullRequestsInput,
         "List pull requests of a repository (state, author, reviewers, age, labels)."),
        ("github.get_pull_request", tools.get_pull_request, s.GetPullRequestInput,
         "Get one pull request with its reviews (approved / changes requested / pending)."),
        ("github.search_pull_requests", tools.search_pull_requests, s.SearchPullRequestsInput,
         "Search pull requests across allowed repositories, e.g. by Jira key 'PHX-7'."),
        ("github.list_commits", tools.list_commits, s.ListCommitsInput,
         "List recent commits of a repository, optionally by branch, author or date range."),
        ("github.list_workflow_runs", tools.list_workflow_runs, s.ListWorkflowRunsInput,
         "List CI/CD (GitHub Actions) runs with status and conclusion, e.g. failing builds."),
        ("github.get_commit_checks", tools.get_commit_checks, s.GetCommitChecksInput,
         "Summarize CI checks (passed/failed) for a branch or commit."),
    ):  # fmt: skip
        registry.register(
            name, handler, description=description, input_model=model,
            operation=READ, permission=Permission.PROJECTS_READ,
        )  # fmt: skip
    registry.register(
        "github.add_pr_comment",
        tools.add_pr_comment,
        description="Add a comment to a pull request.",
        input_model=s.AddPullRequestCommentInput,
        operation=WRITE,
        permission=Permission.PROJECTS_WRITE,
    )


def register_keka_tools(registry: ToolRegistry, tools: KekaTools) -> None:
    s = keka_schemas
    for name, handler, model, description in (
        ("keka.search_employees", tools.search_employees, s.SearchEmployeesInput,
         "Find employees (name, email, job title, department, manager) in the HR system."),
        ("keka.get_employee", tools.get_employee, s.GetEmployeeInput,
         "Get one employee's work profile by Keka employee id."),
        ("keka.get_leave_requests", tools.get_leave_requests, s.DateRangeInput,
         "Leave requests overlapping a date range (max 90 days), optionally for given employees. "
         "Use for availability and capacity questions."),
        ("keka.get_leave_balances", tools.get_leave_balances, s.GetLeaveBalancesInput,
         "Remaining leave balances, optionally for given employees."),
        ("keka.get_attendance", tools.get_attendance, s.DateRangeInput,
         "Attendance records in a date range (max 90 days), optionally for given employees."),
    ):  # fmt: skip
        registry.register(
            name, handler, description=description, input_model=model,
            operation=READ, permission=Permission.PEOPLE_READ,
        )  # fmt: skip


def build_tool_registry(
    settings: Settings,
    hub: IntegrationHub | None = None,
    *,
    jira_tools: JiraTools | None = None,
    email_tools: EmailTools | None = None,
    github_tools: GitHubTools | None = None,
    keka_tools: KekaTools | None = None,
    knowledge_tools: KnowledgeTools | None = None,
    analytics_tools: AnalyticsTools | None = None,
) -> ToolRegistry:
    """Register tools for every active integration (explicit tool objects win)."""
    hub = hub or build_integration_hub(settings)
    registry = ToolRegistry(
        default_timeout=settings.tool_timeout_seconds,
        read_retries=settings.tool_read_retries,
        retry_backoff=settings.tool_retry_backoff_seconds,
    )

    if jira_tools is None and hub.jira is not None:
        jira_tools = JiraTools(service=hub.jira)
    if github_tools is None and hub.github is not None:
        github_tools = GitHubTools(service=hub.github)
    if keka_tools is None and hub.keka is not None:
        keka_tools = KekaTools(service=hub.keka)
    if email_tools is None and hub.email is not None:
        email_tools = EmailTools(
            service=hub.email, allowed_recipient_domains=settings.gmail_allowed_recipient_domains
        )

    if jira_tools is not None:
        register_jira_tools(registry, jira_tools)
    if github_tools is not None:
        register_github_tools(registry, github_tools)
    if keka_tools is not None:
        register_keka_tools(registry, keka_tools)
    if email_tools is not None:
        register_email_tools(registry, email_tools)
    if knowledge_tools is not None:
        registry.register(
            "knowledge.search",
            knowledge_tools.search,
            description=(
                "Search the organization's internal documents (policies, runbooks, specs, "
                "meeting notes) the user is allowed to read. Returns cited passages."
            ),
            input_model=SearchKnowledgeInput,
            operation=READ,
            permission=Permission.KNOWLEDGE_READ,
        )
    if analytics_tools is not None:
        registry.register(
            "analytics.list_projects",
            analytics_tools.list_projects,
            description="List projects configured for analytics (Jira key, name, repos).",
            input_model=ListProjectsInput,
            operation=READ,
            permission=Permission.ANALYTICS_READ,
        )
        registry.register(
            "analytics.project_health",
            analytics_tools.project_health,
            description=(
                "Computed project health: risk score (0-100) with evidence per factor, sprint "
                "completion forecast, workload vs capacity including leave, reassignment "
                "suggestions, and Jira/GitHub contradictions. Prefer this for 'is X at risk', "
                "'are we on track', 'who is overloaded' questions."
            ),
            input_model=ProjectHealthInput,
            operation=READ,
            permission=Permission.ANALYTICS_READ,
        )

    return registry
