"""Add personal product workspaces and short-lived proxy credentials.

Revision ID: 0014_workspaces
Revises: 0013_pdf_annotation_style
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_workspaces"
down_revision: str | None = "0013_pdf_annotation_style"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("product", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("sync_state", sa.String(length=24), nullable=False),
        sa.Column("usage_bytes", sa.BigInteger(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("task_arn", sa.String(length=1024)),
        sa.Column("target_url", sa.String(length=1024)),
        sa.Column("storage_prefix", sa.String(length=1024), nullable=False),
        sa.Column("environment_file_key", sa.String(length=1024)),
        sa.Column("callback_token_sha256", sa.String(length=64)),
        sa.Column("session_started_at", sa.DateTime(timezone=True)),
        sa.Column("session_expires_at", sa.DateTime(timezone=True)),
        sa.Column("stopped_at", sa.DateTime(timezone=True)),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("failure_code", sa.String(length=64)),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'stopping', 'stopped', 'failed', 'expired')",
            name="ck_workspaces_status",
        ),
        sa.CheckConstraint(
            "sync_state IN ('pending', 'syncing', 'synced', 'quota_exceeded', 'failed')",
            name="ck_workspaces_sync_state",
        ),
        sa.CheckConstraint("usage_bytes >= 0", name="ck_workspaces_usage_bytes"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_user_id", "product", name="uq_workspaces_owner_product"),
    )
    op.create_index("ix_workspaces_status", "workspaces", ["status"])
    op.create_table(
        "workspace_launch_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("token_sha256", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workspace_launch_grants_token",
        "workspace_launch_grants",
        ["token_sha256"],
        unique=True,
    )
    op.create_table(
        "workspace_proxy_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("token_sha256", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workspace_proxy_sessions_token",
        "workspace_proxy_sessions",
        ["token_sha256"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_workspace_proxy_sessions_token", table_name="workspace_proxy_sessions")
    op.drop_table("workspace_proxy_sessions")
    op.drop_index("ix_workspace_launch_grants_token", table_name="workspace_launch_grants")
    op.drop_table("workspace_launch_grants")
    op.drop_index("ix_workspaces_status", table_name="workspaces")
    op.drop_table("workspaces")
