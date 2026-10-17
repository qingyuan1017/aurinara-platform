"""Add product-managed role catalog metadata.

Revision ID: 0004_product_role_registry
Revises: 0003_edc_system_admin
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_product_role_registry"
down_revision: str | None = "0003_edc_system_admin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "roles",
        sa.Column("scope_level", sa.String(length=16), nullable=False, server_default="system"),
    )
    op.add_column(
        "roles",
        sa.Column("assignable", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "roles",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
    )
    op.add_column("roles", sa.Column("source_role_id", sa.Uuid(), nullable=True))
    op.create_unique_constraint(
        "uq_roles_product_source_role", "roles", ["product", "source_role_id"]
    )
    op.create_check_constraint(
        "ck_roles_scope_level", "roles", "scope_level IN ('system', 'study', 'site')"
    )
    op.create_check_constraint("ck_roles_status", "roles", "status IN ('active', 'inactive')")

    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE roles SET assignable = false "
            "WHERE lower(name) LIKE '% service' OR product = 'platform'"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO permissions (code, product, description) "
            "VALUES ('edc.role.sync', 'aurinara-edc', "
            "'Register the reviewed Aurinara EDC role catalog') "
            "ON CONFLICT (code) DO NOTHING"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_code) "
            "SELECT id, 'edc.role.sync' FROM roles "
            "WHERE product = 'aurinara-edc' AND name = 'aurinara-edc Service' "
            "ON CONFLICT DO NOTHING"
        )
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("DELETE FROM role_permissions WHERE permission_code = 'edc.role.sync'")
    )
    connection.execute(sa.text("DELETE FROM permissions WHERE code = 'edc.role.sync'"))
    op.drop_constraint("ck_roles_status", "roles", type_="check")
    op.drop_constraint("ck_roles_scope_level", "roles", type_="check")
    op.drop_constraint("uq_roles_product_source_role", "roles", type_="unique")
    op.drop_column("roles", "source_role_id")
    op.drop_column("roles", "status")
    op.drop_column("roles", "assignable")
    op.drop_column("roles", "scope_level")
