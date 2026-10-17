"""Control-plane persistence models; product databases never import these models."""

import enum
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
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
    sponsor: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default=StudyStatus.active, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Site(Base):
    """Canonical Site identity registered by the CTMS-owning EDC product."""

    __tablename__ = "sites"
    __table_args__ = (
        Index("ix_sites_study_status_name", "study_id", "status", "name"),
        CheckConstraint("status IN ('active', 'inactive')", name="ck_sites_status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    study_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("studies.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(100))
    city: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Permission(Base):
    __tablename__ = "permissions"

    __table_args__ = (
        CheckConstraint("status IN ('active', 'inactive')", name="ck_permissions_status"),
    )

    code: Mapped[str] = mapped_column(String(160), primary_key=True)
    product: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    module: Mapped[str | None] = mapped_column(String(64))
    allowed_scope_levels: Mapped[list[str]] = mapped_column(
        JsonType, nullable=False, default=lambda: ["system", "study", "site"]
    )
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("product", "name", name="uq_roles_product_name"),
        UniqueConstraint("product", "source_role_id", name="uq_roles_product_source_role"),
        CheckConstraint("scope_level IN ('system', 'study', 'site')", name="ck_roles_scope_level"),
        CheckConstraint("status IN ('active', 'inactive')", name="ck_roles_status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    product: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    scope_level: Mapped[str] = mapped_column(String(16), default="system", nullable=False)
    assignable: Mapped[bool] = mapped_column(default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    source_role_id: Mapped[UUID | None] = mapped_column(Uuid)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    permissions: Mapped[list["RolePermission"]] = relationship(
        back_populates="role", cascade="all, delete-orphan"
    )

    @property
    def permission_codes(self) -> list[str]:
        return sorted(item.permission_code for item in self.permissions)


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


class RoleChange(Base):
    """Immutable evidence for centrally managed role configuration changes."""

    __tablename__ = "role_changes"
    __table_args__ = (Index("ix_role_changes_role_created", "role_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    role_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False
    )
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    previous_value: Mapped[dict[str, object] | None] = mapped_column(JsonType)
    new_value: Mapped[dict[str, object]] = mapped_column(JsonType, nullable=False)
    policy_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


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
    user: Mapped[User] = relationship(foreign_keys=[user_id])


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
    upload_authorization_action: Mapped[str | None] = mapped_column(String(160))
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
        Index(
            "uq_object_references_product_idempotency",
            "product",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
            sqlite_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    object_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("stored_objects.id", ondelete="RESTRICT"), nullable=False
    )
    product: Mapped[str] = mapped_column(String(64), nullable=False)
    authorization_action: Mapped[str | None] = mapped_column(String(160))
    external_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RepositoryNode(Base):
    """A folder or logical file in the study-scoped Repository namespace."""

    __tablename__ = "repository_nodes"
    __table_args__ = (
        CheckConstraint("kind IN ('folder', 'file')", name="ck_repository_nodes_kind"),
        CheckConstraint(
            "lifecycle_status IN ('active', 'archived')",
            name="ck_repository_nodes_lifecycle_status",
        ),
        Index(
            "uq_repository_nodes_active_sibling_name",
            "study_id",
            "parent_id",
            "normalized_name",
            unique=True,
            postgresql_where=text("lifecycle_status = 'active'"),
            sqlite_where=text("lifecycle_status = 'active'"),
        ),
        Index("ix_repository_nodes_browse", "study_id", "parent_id", "lifecycle_status"),
        Index("ix_repository_nodes_source", "study_id", "source_product", "module"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    study_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("studies.id", ondelete="RESTRICT"), nullable=False
    )
    parent_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("repository_nodes.id", ondelete="RESTRICT")
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_product: Mapped[str] = mapped_column(String(64), nullable=False)
    module: Mapped[str | None] = mapped_column(String(64))
    classification: Mapped[str] = mapped_column(String(64), nullable=False)
    required_read_action: Mapped[str] = mapped_column(String(160), nullable=False)
    required_write_action: Mapped[str] = mapped_column(String(160), nullable=False)
    site_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("sites.id", ondelete="RESTRICT"))
    source_resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_resource_id: Mapped[str | None] = mapped_column(String(255))
    source_status: Mapped[str] = mapped_column(String(64), nullable=False, default="available")
    retention_metadata: Mapped[dict[str, object]] = mapped_column(
        JsonType, nullable=False, default=dict
    )
    lifecycle_status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    current_version_number: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    updated_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RepositoryFileVersion(Base):
    """Append-only logical-file version pointing at immutable stored bytes."""

    __tablename__ = "repository_file_versions"
    __table_args__ = (
        UniqueConstraint("node_id", "version_number", name="uq_repository_file_version"),
        Index("ix_repository_versions_object", "object_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    node_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("repository_nodes.id", ondelete="RESTRICT"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    object_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("stored_objects.id", ondelete="RESTRICT"), nullable=False
    )
    object_reference_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("object_references.id", ondelete="RESTRICT")
    )
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RepositorySourceLink(Base):
    """Product provenance attached to a logical file version."""

    __tablename__ = "repository_source_links"
    __table_args__ = (
        UniqueConstraint(
            "product", "external_reference", name="uq_repository_source_product_external"
        ),
        Index("ix_repository_source_links_node_active", "node_id", "released_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    node_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("repository_nodes.id", ondelete="RESTRICT"), nullable=False
    )
    version_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("repository_file_versions.id", ondelete="RESTRICT"), nullable=False
    )
    object_reference_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("object_references.id", ondelete="RESTRICT"), nullable=False
    )
    product: Mapped[str] = mapped_column(String(64), nullable=False)
    external_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_status: Mapped[str] = mapped_column(String(64), nullable=False)
    provenance: Mapped[dict[str, object]] = mapped_column(JsonType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RepositoryNodeRevision(Base):
    """Immutable evidence of each Repository namespace or version transition."""

    __tablename__ = "repository_node_revisions"
    __table_args__ = (
        UniqueConstraint("node_id", "revision", name="uq_repository_node_revision"),
        Index("ix_repository_node_revisions_created", "node_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    node_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("repository_nodes.id", ondelete="RESTRICT"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    snapshot: Mapped[dict[str, object]] = mapped_column(JsonType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
