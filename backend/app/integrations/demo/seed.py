"""Seed data for the demo company, shared by run_demo, tests and evaluations."""

from importlib import resources

DEMO_PROJECT_KEY = "PHX"

PHX_CONFIG = {
    "name": "Phoenix Payments Revamp",
    "github_repos": ["acme/payments", "acme/checkout-web"],
    "default_branch": "main",
    "active_sprint_id": 42,
    "links": {
        "pagerduty_services": ["payments-api"],
        "sentry_projects": ["payments-api", "checkout-web"],
        "argocd_apps": ["payments-api-prod", "payments-api-staging"],
        "sonarqube_projects": ["acme_payments"],
        "jenkins_jobs": ["payments/release"],
    },
    "members": [
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
    ],
    "report_recipients": ["leadership@acme.example"],
}

# (file, title, visibility, allowed roles) — the contract is restricted to project managers.
DEMO_DOCUMENTS = [
    ("incident-escalation-policy.md", "Incident Escalation Policy", "organization", []),
    ("release-process.md", "Release Process", "organization", []),
    ("gateway-vendor-contract.md", "Gateway Vendor Contract Summary", "roles", ["project_manager"]),
]


def read_document(filename: str) -> str:
    return (
        resources.files("backend.app.integrations.demo")
        .joinpath("knowledge", filename)
        .read_text("utf-8")
    )
