"""Add the canonical Site registry.

Revision ID: 0005_canonical_sites
Revises: 0004_product_role_registry
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_canonical_sites"
down_revision: str | None = "0004_product_role_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sites",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "study_id",
            sa.Uuid(),
            sa.ForeignKey("studies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("country", sa.String(length=100)),
        sa.Column("state", sa.String(length=100)),
        sa.Column("city", sa.String(length=100)),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('active', 'inactive')", name="ck_sites_status"),
    )
    op.create_index(
        "ix_sites_study_status_name",
        "sites",
        ["study_id", "status", "name"],
    )

    connection = op.get_bind()
    connection.execute(
        sa.text(
            "INSERT INTO permissions (code, product, description) "
            "VALUES ('edc.site.sync', 'aurinara-edc', "
            "'Register CTMS-created canonical Sites') "
            "ON CONFLICT (code) DO NOTHING"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_code) "
            "SELECT id, 'edc.site.sync' FROM roles "
            "WHERE product = 'aurinara-edc' AND name = 'aurinara-edc Service' "
            "ON CONFLICT DO NOTHING"
        )
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("DELETE FROM role_permissions WHERE permission_code = 'edc.site.sync'")
    )
    connection.execute(sa.text("DELETE FROM permissions WHERE code = 'edc.site.sync'"))
    op.drop_index("ix_sites_study_status_name", table_name="sites")
    op.drop_table("sites")
