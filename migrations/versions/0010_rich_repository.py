"""Add rich Repository profiles, jobs, annotations, uploads, and Office sessions.

Revision ID: 0010_rich_repository
Revises: 0009_platform_owned_roles
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_rich_repository"
down_revision: str | None = "0009_platform_owned_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_type = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        "repository_file_versions",
        sa.Column("derived_from_version_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_repository_versions_derived_from",
        "repository_file_versions",
        "repository_file_versions",
        ["derived_from_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "repository_version_profiles",
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("file_kind", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column(
            "detected_media_type",
            sa.String(255),
            nullable=False,
            server_default="application/octet-stream",
        ),
        sa.Column("processing_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("metadata_json", json_type, nullable=False),
        sa.Column("preview_object_id", sa.Uuid(), nullable=True),
        sa.Column("analyzer_version", sa.String(64), nullable=False, server_default="1"),
        sa.Column("failure_code", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "processing_status IN ('pending', 'ready', 'failed', 'unsupported')",
            name="ck_repository_version_profiles_status",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"], ["repository_file_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["preview_object_id"], ["stored_objects.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("version_id"),
    )
    op.create_index(
        "ix_repository_version_profiles_status",
        "repository_version_profiles",
        ["processing_status", "updated_at"],
    )

    op.create_table(
        "repository_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_type", sa.String(32), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("result", json_type, nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'failed')", name="ck_repository_jobs_state"
        ),
        sa.ForeignKeyConstraint(
            ["version_id"], ["repository_file_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_repository_jobs_idempotency"),
    )
    op.create_index(
        "ix_repository_jobs_claim",
        "repository_jobs",
        ["state", "available_at", "lease_expires_at"],
    )

    op.create_table(
        "repository_pdf_annotations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("geometry", json_type, nullable=False),
        sa.Column("selected_text", sa.Text(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("category", sa.String(64), nullable=False, server_default="comment"),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("revision > 0", name="ck_repository_pdf_annotations_revision"),
        sa.ForeignKeyConstraint(
            ["version_id"], ["repository_file_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_repository_pdf_annotations_version",
        "repository_pdf_annotations",
        ["version_id", "deleted_at"],
    )
    op.create_table(
        "repository_pdf_annotation_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("annotation_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["annotation_id"], ["repository_pdf_annotations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("annotation_id", "revision", name="uq_pdf_annotation_revision"),
    )

    op.create_table(
        "repository_office_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("base_version_id", sa.Uuid(), nullable=False),
        sa.Column("document_key", sa.String(128), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="active"),
        sa.Column("editable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("callback_token_id", sa.String(64), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_editor_id", sa.Uuid(), nullable=True),
        sa.Column("last_saved_sha256", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('active', 'saved', 'closed', 'expired')",
            name="ck_repository_office_sessions_state",
        ),
        sa.ForeignKeyConstraint(["node_id"], ["repository_nodes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["base_version_id"], ["repository_file_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["last_editor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_key"),
    )
    op.create_index(
        "ix_repository_office_sessions_node_state",
        "repository_office_sessions",
        ["node_id", "state"],
    )
    op.create_table(
        "repository_office_participants",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"], ["repository_office_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("session_id", "user_id"),
        sa.UniqueConstraint("session_id", "user_id", name="uq_repository_office_participant"),
    )

    op.create_table(
        "repository_uploads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column("study_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("replacement_node_id", sa.Uuid(), nullable=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("classification", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["object_id"], ["stored_objects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["study_id"], ["studies.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["parent_id"], ["repository_nodes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["replacement_node_id"], ["repository_nodes.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_repository_uploads_object_id", "repository_uploads", ["object_id"])

    # Existing immutable versions enter the same analysis pipeline as new ones.
    op.execute(
        sa.text(
            """
            INSERT INTO repository_version_profiles
                (version_id, file_kind, detected_media_type, processing_status,
                 metadata_json, analyzer_version, created_at, updated_at)
            SELECT id, 'unknown', 'application/octet-stream', 'pending', '{}',
                   '1', created_at, created_at
            FROM repository_file_versions
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO repository_jobs
                (id, job_type, version_id, requested_by, idempotency_key, state,
                 payload, result, attempts, max_attempts, available_at,
                 created_at, updated_at)
            SELECT id, 'analyze', id, created_by,
                   'analyze:' || CAST(id AS VARCHAR), 'queued', '{}', '{}',
                   0, 3, created_at, created_at, created_at
            FROM repository_file_versions
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_repository_uploads_object_id", table_name="repository_uploads")
    op.drop_table("repository_uploads")
    op.drop_table("repository_office_participants")
    op.drop_index(
        "ix_repository_office_sessions_node_state", table_name="repository_office_sessions"
    )
    op.drop_table("repository_office_sessions")
    op.drop_table("repository_pdf_annotation_revisions")
    op.drop_index("ix_repository_pdf_annotations_version", table_name="repository_pdf_annotations")
    op.drop_table("repository_pdf_annotations")
    op.drop_index("ix_repository_jobs_claim", table_name="repository_jobs")
    op.drop_table("repository_jobs")
    op.drop_index("ix_repository_version_profiles_status", table_name="repository_version_profiles")
    op.drop_table("repository_version_profiles")
    op.drop_constraint(
        "fk_repository_versions_derived_from", "repository_file_versions", type_="foreignkey"
    )
    op.drop_column("repository_file_versions", "derived_from_version_id")
