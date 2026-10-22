"""Add the central Playground catalog, conversations, memory binding, and approvals.

Revision ID: 0011_playground
Revises: 0010_rich_repository
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_playground"
down_revision: str | None = "0010_rich_repository"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_type = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "playground_agents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("product_codes", json_type, nullable=False),
        sa.Column("runtime_arn", sa.String(512), nullable=False),
        sa.Column("memory_id", sa.String(255)),
        sa.Column("runtime_qualifier", sa.String(100), nullable=False, server_default="DEFAULT"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_playground_agents_revision"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_playground_agents_key"),
    )
    op.create_table(
        "playground_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("product", sa.String(64), nullable=False),
        sa.Column("principal_issuer", sa.String(512), nullable=False),
        sa.Column("principal_subject", sa.String(255), nullable=False),
        sa.Column("runtime_session_id", sa.String(128), nullable=False),
        sa.Column("title", sa.String(160)),
        sa.Column("state", sa.String(32), nullable=False, server_default="active"),
        sa.Column("pending_interrupt_id", sa.String(255)),
        sa.Column("pending_tool_name", sa.String(255)),
        sa.Column("pending_arguments_sha256", sa.String(64)),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('active', 'awaiting_approval', 'archived', 'failed')",
            name="ck_playground_conversations_state",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["playground_agents.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("runtime_session_id"),
    )
    op.create_index(
        "ix_playground_conversations_owner",
        "playground_conversations",
        ["principal_issuer", "principal_subject"],
    )
    op.create_table(
        "playground_approval_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("interrupt_id", sa.String(255), nullable=False),
        sa.Column("tool_name", sa.String(255), nullable=False),
        sa.Column("arguments_sha256", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("principal_issuer", sa.String(512), nullable=False),
        sa.Column("principal_subject", sa.String(255), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('approve', 'deny')", name="ck_playground_approval_decision"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["playground_conversations.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id", "interrupt_id", name="uq_playground_approval_interrupt"
        ),
    )


def downgrade() -> None:
    op.drop_table("playground_approval_events")
    op.drop_index("ix_playground_conversations_owner", table_name="playground_conversations")
    op.drop_table("playground_conversations")
    op.drop_table("playground_agents")
