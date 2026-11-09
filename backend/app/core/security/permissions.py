"""Roles, permissions and the agent's permission boundary.

Humans get permissions from their role. When the AI agent acts for a user it gets the
*intersection* of that user's permissions and ``AGENT_SCOPES`` — it can never do more
than the user, and some things (approving actions, managing users, reading the audit
log) it can never do at all. Every action records whether a human or the agent did it.
"""

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class Role(StrEnum):
    ADMIN = "admin"
    PROJECT_MANAGER = "project_manager"
    TEAM_LEAD = "team_lead"
    MEMBER = "member"
    AUDITOR = "auditor"


class Permission(StrEnum):
    AGENT_QUERY = "agent:query"
    PROJECTS_READ = "projects:read"
    PROJECTS_WRITE = "projects:write"
    EMAIL_READ = "email:read"
    EMAIL_SEND = "email:send"
    PEOPLE_READ = "people:read"
    ANALYTICS_READ = "analytics:read"
    REPORTS_READ = "reports:read"
    REPORTS_GENERATE = "reports:generate"
    KNOWLEDGE_READ = "knowledge:read"
    KNOWLEDGE_MANAGE = "knowledge:manage"
    APPROVALS_DECIDE = "approvals:decide"
    AUDIT_READ = "audit:read"
    USERS_MANAGE = "users:manage"


P = Permission

_MEMBER = frozenset(
    {
        P.AGENT_QUERY,
        P.PROJECTS_READ,
        P.EMAIL_READ,
        P.KNOWLEDGE_READ,
        P.REPORTS_READ,
    }
)

_TEAM_LEAD = _MEMBER | {
    P.PROJECTS_WRITE,
    P.EMAIL_SEND,
    P.PEOPLE_READ,
    P.ANALYTICS_READ,
    P.REPORTS_GENERATE,
    P.APPROVALS_DECIDE,
}

_PROJECT_MANAGER = _TEAM_LEAD | {P.KNOWLEDGE_MANAGE}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.PROJECT_MANAGER: frozenset(_PROJECT_MANAGER),
    Role.TEAM_LEAD: frozenset(_TEAM_LEAD),
    Role.MEMBER: _MEMBER,
    # Auditors can inspect everything but change nothing and cannot drive the agent.
    Role.AUDITOR: frozenset(
        {P.PROJECTS_READ, P.ANALYTICS_READ, P.REPORTS_READ, P.KNOWLEDGE_READ, P.AUDIT_READ}
    ),
}

# The most the AI agent may ever do, regardless of who it acts for.
AGENT_SCOPES: frozenset[Permission] = frozenset(
    {
        P.PROJECTS_READ,
        P.PROJECTS_WRITE,
        P.EMAIL_READ,
        P.EMAIL_SEND,
        P.PEOPLE_READ,
        P.ANALYTICS_READ,
        P.REPORTS_READ,
        P.REPORTS_GENERATE,
        P.KNOWLEDGE_READ,
    }
)

# Agent use of these changes external systems, so it must go through human approval.
APPROVAL_REQUIRED: frozenset[Permission] = frozenset({P.PROJECTS_WRITE, P.EMAIL_SEND})


def permissions_for(role: Role) -> frozenset[Permission]:
    return ROLE_PERMISSIONS[role]


ActorType = Literal["human", "agent"]


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is performing an action, and with which permissions."""

    user_id: uuid.UUID
    email: str
    role: Role
    actor_type: ActorType
    permissions: frozenset[Permission]

    @classmethod
    def human(cls, user_id: uuid.UUID, email: str, role: Role) -> "Principal":
        return cls(user_id, email, role, "human", permissions_for(role))

    def as_agent(self) -> "Principal":
        """The agent acting on behalf of this user: never more than the user can do."""
        return Principal(
            self.user_id,
            self.email,
            self.role,
            "agent",
            self.permissions & AGENT_SCOPES,
        )

    def has(self, *required: Permission) -> bool:
        return all(permission in self.permissions for permission in required)

    def requires_approval(self, permission: Permission) -> bool:
        return self.actor_type == "agent" and permission in APPROVAL_REQUIRED
