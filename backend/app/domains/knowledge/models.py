import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from backend.app.db.types import EmbeddingVector


class Visibility(StrEnum):
    ORGANIZATION = "organization"  # everyone with knowledge:read
    ROLES = "roles"  # only the listed roles (admins always)
    PRIVATE = "private"  # only the owner (admins always)


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "documents"

    title: Mapped[str] = mapped_column(String(300))
    source_type: Mapped[str] = mapped_column(String(32))  # upload | text | demo
    filename: Mapped[str | None] = mapped_column(String(300))
    mime_type: Mapped[str | None] = mapped_column(String(100))
    checksum: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    visibility: Mapped[Visibility] = mapped_column(
        Enum(
            Visibility,
            native_enum=False,
            length=16,
            values_callable=lambda items: [i.value for i in items],
            validate_strings=True,
        ),
        default=Visibility.ORGANIZATION,
        index=True,
    )
    # Stored as ",role_a,role_b," so a portable LIKE filter works on every database.
    allowed_roles_csv: Mapped[str] = mapped_column("allowed_roles", String(300), default="")
    tags: Mapped[list[str]] = mapped_column(default=list)
    char_count: Mapped[int] = mapped_column(default=0)
    chunk_count: Mapped[int] = mapped_column(default=0)
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    extra: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # Filters for search over indexed platform data (Jira, chat, e-mail, …).
    project_key: Mapped[str | None] = mapped_column(String(32), index=True)
    occurred_at: Mapped[datetime | None] = mapped_column(index=True)

    @property
    def allowed_roles(self) -> list[str]:
        return [r for r in self.allowed_roles_csv.split(",") if r]

    @allowed_roles.setter
    def allowed_roles(self, roles: list[str]) -> None:
        self.allowed_roles_csv = f",{','.join(sorted(set(roles)))}," if roles else ""

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class DocumentChunk(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "document_chunks"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int] = mapped_column(default=0)
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingVector())
    embedding_model: Mapped[str | None] = mapped_column(String(200), index=True)

    document: Mapped[Document] = relationship(back_populates="chunks")
