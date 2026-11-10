"""Registry of every ORM model, so ``Base.metadata`` is complete for Alembic and tests.

Models live in their domains; import them from here or from the domain module.
"""

from backend.app.domains.agent.models import AgentRun, RunStatus
from backend.app.domains.analytics.models import Project, ProjectSignal
from backend.app.domains.governance.approvals.models import ApprovalRequest, ApprovalStatus
from backend.app.domains.governance.audit.models import AuditChainHead, AuditEvent
from backend.app.domains.identity.models import ApiToken, RefreshToken, User
from backend.app.domains.knowledge.models import Document, DocumentChunk, Visibility
from backend.app.domains.reporting.models import Report

__all__ = [
    "AgentRun",
    "ApiToken",
    "ApprovalRequest",
    "ApprovalStatus",
    "AuditChainHead",
    "AuditEvent",
    "Document",
    "DocumentChunk",
    "Project",
    "ProjectSignal",
    "RefreshToken",
    "Report",
    "RunStatus",
    "User",
    "Visibility",
]
