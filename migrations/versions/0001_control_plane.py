"""Create the shared platform control-plane schema.

Revision ID: 0001_control_plane
Revises: None
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_control_plane"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("issuer", sa.String(512), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320)),
        sa.Column("display_name", sa.String(255)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("issuer", "subject", name="uq_users_issuer_subject"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "studies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(100), nullable=False, unique=True),
        sa.Column("display_name", sa.String(500), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "permissions",
        sa.Column("code", sa.String(160), primary_key=True),
        sa.Column("product", sa.String(64), nullable=False),
        sa.Column("description", sa.Text()),
    )
    op.create_index("ix_permissions_product", "permissions", ["product"])
    op.create_table(
        "roles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("product", sa.String(64), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("product", "name", name="uq_roles_product_name"),
    )
    op.create_table(
        "role_permissions",
        sa.Column(
            "role_id", sa.Uuid(), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "permission_code",
            sa.String(160),
            sa.ForeignKey("permissions.code", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_table(
        "role_assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role_id", sa.Uuid(), sa.ForeignKey("roles.id"), nullable=False),
        sa.Column("study_id", sa.Uuid(), sa.ForeignKey("studies.id")),
        sa.Column("site_id", sa.Uuid()),
        sa.Column("assigned_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "user_id", "role_id", "study_id", "site_id", name="uq_role_assignment_scope"
        ),
    )
    op.create_index("ix_role_assignments_user_study", "role_assignments", ["user_id", "study_id"])
    op.create_table(
        "policy_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("event_type", sa.String(160), nullable=False),
        sa.Column("aggregate_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_outbox_events_event_type", "outbox_events", ["event_type"])
    op.create_table(
        "stored_objects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("study_id", sa.Uuid(), sa.ForeignKey("studies.id"), nullable=False),
        sa.Column("owner_product", sa.String(64), nullable=False),
        sa.Column("classification", sa.String(64), nullable=False),
        sa.Column("sha256", sa.String(64)),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("object_key", sa.String(1024), nullable=False, unique=True),
        sa.Column("multipart_upload_id", sa.String(1024)),
        sa.Column("expected_part_count", sa.Integer()),
        sa.Column("upload_expires_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("retention_until", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_stored_objects_study_status", "stored_objects", ["study_id", "status"])
    op.create_index(
        "uq_stored_objects_study_sha256_available",
        "stored_objects",
        ["study_id", "sha256"],
        unique=True,
        postgresql_where=sa.text("status = 'available'"),
    )
    op.create_table(
        "object_references",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "object_id",
            sa.Uuid(),
            sa.ForeignKey("stored_objects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("product", sa.String(64), nullable=False),
        sa.Column("external_reference", sa.String(255), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "product", "external_reference", name="uq_object_references_product_external"
        ),
    )
    op.execute(
        sa.text(
            "INSERT INTO policy_state (id, version, updated_at) VALUES (1, 1, CURRENT_TIMESTAMP)"
        )
    )


def downgrade() -> None:
    op.drop_table("object_references")
    op.drop_index("uq_stored_objects_study_sha256_available", table_name="stored_objects")
    op.drop_index("ix_stored_objects_study_status", table_name="stored_objects")
    op.drop_table("stored_objects")
    op.drop_index("ix_outbox_events_event_type", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_table("policy_state")
    op.drop_index("ix_role_assignments_user_study", table_name="role_assignments")
    op.drop_table("role_assignments")
    op.drop_table("role_permissions")
    op.drop_table("roles")
    op.drop_index("ix_permissions_product", table_name="permissions")
    op.drop_table("permissions")
    op.drop_table("studies")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
