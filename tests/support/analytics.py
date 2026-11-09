from backend.app.db.models import Project

PHX_MEMBERS = [
    {
        "name": "Arjun M",
        "jira_account_id": "acc-arjun",
        "keka_employee_id": "e-002",
        "capacity_points": 8,
    },
    {
        "name": "Meera S",
        "jira_account_id": "acc-meera",
        "keka_employee_id": "e-005",
        "capacity_points": 10,
    },
    {
        "name": "Sara K",
        "jira_account_id": "acc-sara",
        "keka_employee_id": "e-003",
        "capacity_points": 8,
    },
    {
        "name": "Dev R",
        "jira_account_id": "acc-dev",
        "keka_employee_id": "e-004",
        "capacity_points": 20,
    },
]

PHX_CONFIG = {
    "name": "Phoenix Payments Revamp",
    "github_repos": ["acme/payments", "acme/checkout-web"],
    "default_branch": "main",
    "active_sprint_id": 42,
    "members": PHX_MEMBERS,
    "report_recipients": ["leadership@acme.example"],
}


def phx_project() -> Project:
    return Project(key="PHX", **PHX_CONFIG, is_active=True)
