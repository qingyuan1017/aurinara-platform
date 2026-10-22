"""Control-plane persistence models; product databases never import these models."""

import enum
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
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


class PlaygroundAgent(Base):
    __tablename__ = "playground_agents"
    __table_args__ = (
        UniqueConstraint("runtime_arn", name="uq_playground_agents_runtime_arn"),
        CheckConstraint("revision > 0", name="ck_playground_agents_revision"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    product_codes: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    runtime_arn: Mapped[str] = mapped_column(String(512), nullable=False)
    memory_id: Mapped[str | None] = mapped_column(String(255))
    runtime_qualifier: Mapped[str] = mapped_column(String(100), nullable=False, default="DEFAULT")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class PlaygroundConversation(Base):
    __tablename__ = "playground_conversations"
    __table_args__ = (
        CheckConstraint(
            "state IN ('active', 'awaiting_approval', 'archived', 'failed')",
            name="ck_playground_conversations_state",
        ),
        Index("ix_playground_conversations_owner", "principal_issuer", "principal_subject"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    agent_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("playground_agents.id", ondelete="RESTRICT"), nullable=False
    )
    product: Mapped[str] = mapped_column(String(64), nullable=False)
    principal_issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    principal_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    runtime_session_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    title: Mapped[str | None] = mapped_column(String(160))
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    pending_interrupt_id: Mapped[str | None] = mapped_column(String(255))
    pending_tool_name: Mapped[str | None] = mapped_column(String(255))
    pending_arguments_sha256: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    agent: Mapped[PlaygroundAgent] = relationship()


class PlaygroundApprovalEvent(Base):
    __tablename__ = "playground_approval_events"
    __table_args__ = (
        CheckConstraint("decision IN ('approve', 'deny')", name="ck_playground_approval_decision"),
        UniqueConstraint(
            "conversation_id", "interrupt_id", name="uq_playground_approval_interrupt"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("playground_conversations.id", ondelete="RESTRICT"), nullable=False
    )
    interrupt_id: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    arguments_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    principal_issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    principal_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


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
        UniqueConstraint("key", name="uq_roles_key"),
        UniqueConstraint("product", "name", name="uq_roles_product_name"),
        UniqueConstraint("product", "source_role_id", name="uq_roles_product_source_role"),
        CheckConstraint("scope_level IN ('system', 'study', 'site')", name="ck_roles_scope_level"),
        CheckConstraint(
            "permission_policy IN ('explicit', 'all', 'all_scoped', 'read_scoped')",
            name="ck_roles_permission_policy",
        ),
        CheckConstraint("status IN ('active', 'inactive')", name="ck_roles_status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    key: Mapped[str] = mapped_column(String(160), nullable=False, default=lambda: f"role.{uuid4()}")
    product: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    scope_level: Mapped[str] = mapped_column(String(16), default="system", nullable=False)
    assignable: Mapped[bool] = mapped_column(default=True, nullable=False)
    built_in: Mapped[bool] = mapped_column(default=False, nullable=False)
    permission_policy: Mapped[str] = mapped_column(String(24), default="explicit", nullable=False)
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
    derived_from_version_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("repository_file_versions.id", ondelete="RESTRICT")
    )
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RepositoryVersionProfile(Base):
    """Detected, reproducible metadata for one immutable Repository version."""

    __tablename__ = "repository_version_profiles"
    __table_args__ = (
        CheckConstraint(
            "processing_status IN ('pending', 'ready', 'failed', 'unsupported')",
            name="ck_repository_version_profiles_status",
        ),
        Index("ix_repository_version_profiles_status", "processing_status", "updated_at"),
    )

    version_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("repository_file_versions.id", ondelete="CASCADE"), primary_key=True
    )
    file_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    detected_media_type: Mapped[str] = mapped_column(
        String(255), nullable=False, default="application/octet-stream"
    )
    processing_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    metadata_json: Mapped[dict[str, object]] = mapped_column(JsonType, nullable=False, default=dict)
    preview_object_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("stored_objects.id", ondelete="RESTRICT")
    )
    analyzer_version: Mapped[str] = mapped_column(String(64), nullable=False, default="1")
    failure_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class RepositoryJob(Base):
    """Durable, leased work item for analysis and derived-file creation."""

    __tablename__ = "repository_jobs"
    __table_args__ = (
        CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_repository_jobs_state",
        ),
        UniqueConstraint("idempotency_key", name="uq_repository_jobs_idempotency"),
        Index("ix_repository_jobs_claim", "state", "available_at", "lease_expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    version_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("repository_file_versions.id", ondelete="RESTRICT"), nullable=False
    )
    requested_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    payload: Mapped[dict[str, object]] = mapped_column(JsonType, nullable=False, default=dict)
    result: Mapped[dict[str, object]] = mapped_column(JsonType, nullable=False, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RepositoryPdfAnnotation(Base):
    """Mutable annotation head; every mutation is also written to immutable history."""

    __tablename__ = "repository_pdf_annotations"
    __table_args__ = (
        Index("ix_repository_pdf_annotations_version", "version_id", "deleted_at"),
        CheckConstraint("revision > 0", name="ck_repository_pdf_annotations_revision"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    version_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("repository_file_versions.id", ondelete="RESTRICT"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    geometry: Mapped[dict[str, object]] = mapped_column(JsonType, nullable=False)
    selected_text: Mapped[str | None] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="comment")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    updated_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RepositoryPdfAnnotationRevision(Base):
    __tablename__ = "repository_pdf_annotation_revisions"
    __table_args__ = (
        UniqueConstraint("annotation_id", "revision", name="uq_pdf_annotation_revision"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    annotation_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("repository_pdf_annotations.id", ondelete="RESTRICT"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    snapshot: Mapped[dict[str, object]] = mapped_column(JsonType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RepositoryOfficeSession(Base):
    __tablename__ = "repository_office_sessions"
    __table_args__ = (
        CheckConstraint(
            "state IN ('active', 'saved', 'closed', 'expired')",
            name="ck_repository_office_sessions_state",
        ),
        Index("ix_repository_office_sessions_node_state", "node_id", "state"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    node_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("repository_nodes.id", ondelete="RESTRICT"), nullable=False
    )
    base_version_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("repository_file_versions.id", ondelete="RESTRICT"), nullable=False
    )
    document_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    editable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    callback_token_id: Mapped[str] = mapped_column(String(64), nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_editor_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    last_saved_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class RepositoryOfficeParticipant(Base):
    __tablename__ = "repository_office_participants"
    __table_args__ = (
        UniqueConstraint("session_id", "user_id", name="uq_repository_office_participant"),
    )

    session_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("repository_office_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), primary_key=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RepositoryUpload(Base):
    """Intent captured by the high-level Repository upload API."""

    __tablename__ = "repository_uploads"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    object_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("stored_objects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    study_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("studies.id"), nullable=False)
    parent_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("repository_nodes.id", ondelete="RESTRICT")
    )
    replacement_node_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("repository_nodes.id", ondelete="RESTRICT")
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    classification: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
