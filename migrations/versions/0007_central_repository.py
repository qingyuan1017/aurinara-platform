"""Add the central immutable Repository catalog.

Revision ID: 0007_central_repository
Revises: 0006_platform_managed_roles
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_central_repository"
down_revision: str | None = "0006_platform_managed_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "stored_objects",
        sa.Column("upload_authorization_action", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "object_references",
        sa.Column("authorization_action", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "object_references", sa.Column("idempotency_key", sa.String(length=255), nullable=True)
    )
    op.create_index(
        "uq_object_references_product_idempotency",
        "object_references",
        ["product", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_table(
        "repository_nodes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "study_id", sa.Uuid(), sa.ForeignKey("studies.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "parent_id",
            sa.Uuid(),
            sa.ForeignKey("repository_nodes.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("source_product", sa.String(length=64), nullable=False),
        sa.Column("module", sa.String(length=64), nullable=True),
        sa.Column("classification", sa.String(length=64), nullable=False),
        sa.Column("required_read_action", sa.String(length=160), nullable=False),
        sa.Column("required_write_action", sa.String(length=160), nullable=False),
        sa.Column(
            "site_id", sa.Uuid(), sa.ForeignKey("sites.id", ondelete="RESTRICT"), nullable=True
        ),
        sa.Column("source_resource_type", sa.String(length=100), nullable=False),
        sa.Column("source_resource_id", sa.String(length=255), nullable=True),
        sa.Column("source_status", sa.String(length=64), nullable=False),
        sa.Column(
            "retention_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("lifecycle_status", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("current_version_number", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("updated_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('folder', 'file')", name="ck_repository_nodes_kind"),
        sa.CheckConstraint(
            "lifecycle_status IN ('active', 'archived')",
            name="ck_repository_nodes_lifecycle_status",
        ),
    )
    op.create_index(
        "uq_repository_nodes_active_sibling_name",
        "repository_nodes",
        ["study_id", "parent_id", "normalized_name"],
        unique=True,
        postgresql_where=sa.text("lifecycle_status = 'active'"),
    )
    op.create_index(
        "ix_repository_nodes_browse",
        "repository_nodes",
        ["study_id", "parent_id", "lifecycle_status"],
    )
    op.create_index(
        "ix_repository_nodes_source", "repository_nodes", ["study_id", "source_product", "module"]
    )
    op.create_table(
        "repository_file_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "node_id",
            sa.Uuid(),
            sa.ForeignKey("repository_nodes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column(
            "object_id",
            sa.Uuid(),
            sa.ForeignKey("stored_objects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "object_reference_id",
            sa.Uuid(),
            sa.ForeignKey("object_references.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("node_id", "version_number", name="uq_repository_file_version"),
    )
    op.create_index("ix_repository_versions_object", "repository_file_versions", ["object_id"])
    op.create_table(
        "repository_source_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "node_id",
            sa.Uuid(),
            sa.ForeignKey("repository_nodes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "version_id",
            sa.Uuid(),
            sa.ForeignKey("repository_file_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "object_reference_id",
            sa.Uuid(),
            sa.ForeignKey("object_references.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("product", sa.String(length=64), nullable=False),
        sa.Column("external_reference", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=100), nullable=False),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("source_status", sa.String(length=64), nullable=False),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "product", "external_reference", name="uq_repository_source_product_external"
        ),
    )
    op.create_index(
        "ix_repository_source_links_node_active",
        "repository_source_links",
        ["node_id", "released_at"],
    )
    op.create_table(
        "repository_node_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "node_id",
            sa.Uuid(),
            sa.ForeignKey("repository_nodes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("node_id", "revision", name="uq_repository_node_revision"),
    )
    op.create_index(
        "ix_repository_node_revisions_created",
        "repository_node_revisions",
        ["node_id", "created_at"],
    )
    op.execute(
        sa.text(
            "CREATE FUNCTION prevent_repository_history_mutation() RETURNS trigger AS $$ "
            "BEGIN RAISE EXCEPTION 'repository history is append-only'; END; "
            "$$ LANGUAGE plpgsql"
        )
    )
    for table in (
        "repository_file_versions",
        "repository_source_links",
        "repository_node_revisions",
    ):
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION prevent_repository_history_mutation()"
            )
        )

    connection = op.get_bind()
    for code, description in (
        ("platform.object.upload", "Upload Platform-owned immutable objects"),
        ("platform.object.download", "Download Platform-owned immutable objects"),
        ("platform.object.reference", "Manage Platform-owned object references"),
        ("platform.repository.read", "Browse and download authorized Repository entries"),
        ("platform.repository.write", "Manage authorized Repository entries"),
    ):
        connection.execute(
            sa.text(
                "INSERT INTO permissions "
                "(code, product, description, module, allowed_scope_levels, status) "
                "VALUES (:code, 'platform', :description, 'repository', "
                '\'["system", "study", "site"]\'::jsonb, \'active\') '
                "ON CONFLICT (code) DO NOTHING"
            ),
            {"code": code, "description": description},
        )
        connection.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_code) "
                "SELECT id, :code FROM roles WHERE product = 'platform' "
                "AND name IN ('Platform Administrator', 'Platform Admin') ON CONFLICT DO NOTHING"
            ),
            {"code": code},
        )
    roles = (
        (
            "e65bbdb3-612f-5df4-86e2-7cadadba087a",
            "Repository Reader",
            "Browse and download Repository entries permitted by product roles",
            ("platform.repository.read",),
        ),
        (
            "da128625-af37-5cf8-b87a-c319c99a5328",
            "Repository Contributor",
            "Browse and manage Repository entries permitted by product roles",
            ("platform.repository.read", "platform.repository.write"),
        ),
    )
    for role_id, name, description, permissions in roles:
        connection.execute(
            sa.text(
                "INSERT INTO roles "
                "(id, product, name, description, scope_level, assignable, status, revision, "
                "created_at, updated_at) "
                "VALUES (:id, 'platform', :name, :description, 'study', true, 'active', 1, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
                "ON CONFLICT (product, name) DO NOTHING"
            ),
            {"id": role_id, "name": name, "description": description},
        )
        for permission in permissions:
            connection.execute(
                sa.text(
                    "INSERT INTO role_permissions (role_id, permission_code) "
                    "SELECT id, :permission FROM roles WHERE product = 'platform' AND name = :name "
                    "ON CONFLICT DO NOTHING"
                ),
                {"permission": permission, "name": name},
            )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE role_id IN "
            "(SELECT id FROM roles WHERE product = 'platform' "
            "AND name IN ('Repository Reader', 'Repository Contributor'))"
        )
    )
    connection.execute(
        sa.text(
            "DELETE FROM roles WHERE product = 'platform' "
            "AND name IN ('Repository Reader', 'Repository Contributor')"
        )
    )
    connection.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_code IN "
            "('platform.object.upload', 'platform.object.download', "
            "'platform.object.reference', 'platform.repository.read', "
            "'platform.repository.write')"
        )
    )
    connection.execute(
        sa.text(
            "DELETE FROM permissions WHERE code IN "
            "('platform.object.upload', 'platform.object.download', "
            "'platform.object.reference', 'platform.repository.read', "
            "'platform.repository.write')"
        )
    )
    for table in (
        "repository_file_versions",
        "repository_source_links",
        "repository_node_revisions",
    ):
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS prevent_repository_history_mutation()"))
    op.drop_index("ix_repository_node_revisions_created", table_name="repository_node_revisions")
    op.drop_table("repository_node_revisions")
    op.drop_index("ix_repository_source_links_node_active", table_name="repository_source_links")
    op.drop_table("repository_source_links")
    op.drop_index("ix_repository_versions_object", table_name="repository_file_versions")
    op.drop_table("repository_file_versions")
    op.drop_index("ix_repository_nodes_source", table_name="repository_nodes")
    op.drop_index("ix_repository_nodes_browse", table_name="repository_nodes")
    op.drop_index("uq_repository_nodes_active_sibling_name", table_name="repository_nodes")
    op.drop_table("repository_nodes")
    op.drop_index("uq_object_references_product_idempotency", table_name="object_references")
    op.drop_column("object_references", "idempotency_key")
    op.drop_column("object_references", "authorization_action")
    op.drop_column("stored_objects", "upload_authorization_action")
