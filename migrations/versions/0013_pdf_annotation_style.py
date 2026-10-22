"""Persist PDF annotation typography and colors.

Revision ID: 0013_pdf_annotation_style
Revises: 0012_playground_agent_identity
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_pdf_annotation_style"
down_revision: str | None = "0012_playground_agent_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_type = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        "repository_pdf_annotations",
        sa.Column("style", json_type, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("repository_pdf_annotations", "style")
