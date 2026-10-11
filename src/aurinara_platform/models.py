"""Control-plane persistence models; product databases never import these models."""

import enum
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from aurinara_platform.database import Base

JsonType = JSON().with_variant(JSONB(), "postgresql")


def utc_now() -> datetime:
    return datetime.now(UTC)


class UserStatus(enum.StrEnum):
    active = "active"
    inactive = "inactive"


class StudyStatus(enum.StrEnum):
    active = "active"
    archived = "archived"


class ObjectStatus(enum.StrEnum):
    pending = "pending"
    available = "available"
    rejected = "rejected"
    deleted = "deleted"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_users_issuer_subject"),
        Index("ix_users_email", "email"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default=UserStatus.active, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Study(Base):
    __tablename__ = "studies"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=StudyStatus.active, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Permission(Base):
    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(160), primary_key=True)
    product: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("product", "name", name="uq_roles_product_name"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    product: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    permissions: Mapped[list["RolePermission"]] = relationship(
        back_populates="role", cascade="all, delete-orphan"
    )


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_code: Mapped[str] = mapped_column(
        String(160), ForeignKey("permissions.code", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[Role] = relationship(back_populates="permissions")
    permission: Mapped[Permission] = relationship()


class RoleAssignment(Base):
    """A system, study, or site-scoped role assignment."""

    __tablename__ = "role_assignments"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "role_id", "study_id", "site_id", name="uq_role_assignment_scope"
        ),
        Index("ix_role_assignments_user_study", "user_id", "study_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    role_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("roles.id"), nullable=False)
    study_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("studies.id"))
    site_id: Mapped[UUID | None] = mapped_column(Uuid)
    assigned_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    role: Mapped[Role] = relationship()


class PolicyState(Base):
    __tablename__ = "policy_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    version: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    event_type: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JsonType, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StoredObject(Base):
    __tablename__ = "stored_objects"
    __table_args__ = (
        Index(
            "uq_stored_objects_study_sha256_available",
            "study_id",
            "sha256",
            unique=True,
            postgresql_where=text("status = 'available'"),
            sqlite_where=text("status = 'available'"),
        ),
        Index("ix_stored_objects_study_status", "study_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    study_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("studies.id"), nullable=False)
    owner_product: Mapped[str] = mapped_column(String(64), nullable=False)
    classification: Mapped[str] = mapped_column(String(64), nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    multipart_upload_id: Mapped[str | None] = mapped_column(String(1024))
    expected_part_count: Mapped[int | None] = mapped_column(Integer)
    upload_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default=ObjectStatus.pending, nullable=False)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def retention_state(self) -> str:
        if self.retention_until is None:
            return "unrestricted"
        value = self.retention_until
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return "retained" if utc_now() < value else "expired"


class ObjectReference(Base):
    __tablename__ = "object_references"
    __table_args__ = (
        UniqueConstraint(
            "product", "external_reference", name="uq_object_references_product_external"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    object_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("stored_objects.id", ondelete="RESTRICT"), nullable=False
    )
    product: Mapped[str] = mapped_column(String(64), nullable=False)
    external_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
