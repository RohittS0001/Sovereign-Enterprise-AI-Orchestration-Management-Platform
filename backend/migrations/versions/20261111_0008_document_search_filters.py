"""Search filters on documents: the project and the time an indexed record happened.

Indexed platform data (Jira issues, chat threads, e-mails, incidents, …) is filtered by
source, project and date in SQL before ranking, like access control.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-11 14:10:45.385483+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.app.db.types import UTCDateTime

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.add_column(sa.Column("project_key", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("occurred_at", UTCDateTime(timezone=True), nullable=True))
        batch_op.create_index(batch_op.f("ix_documents_occurred_at"), ["occurred_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_documents_project_key"), ["project_key"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_documents_project_key"))
        batch_op.drop_index(batch_op.f("ix_documents_occurred_at"))
        batch_op.drop_column("occurred_at")
        batch_op.drop_column("project_key")
