"""Make role identity and permission policy Platform-owned.

Revision ID: 0009_platform_owned_roles
Revises: 0008_aurinara_permissions
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_platform_owned_roles"
down_revision: str | None = "0008_aurinara_permissions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("roles", sa.Column("key", sa.String(length=160), nullable=True))
    op.add_column(
        "roles",
        sa.Column("built_in", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "roles",
        sa.Column(
            "permission_policy",
            sa.String(length=24),
            nullable=False,
            server_default="explicit",
        ),
    )

    # Role identity and composition now belong to Platform. The previous
    # product-defined rows cannot be translated reliably into the new stable
    # role catalog, so replace the old authorization state instead of exposing
    # synthetic ``legacy.<uuid>`` roles. Core roles are materialized at app
    # startup and service roles are recreated by bootstrap_service_accounts.
    op.execute(
        sa.text(
            "DELETE FROM outbox_events "
            "WHERE event_type IN ("
            "'platform.assignment.changed.v1', "
            "'platform.role.changed.v1', "
            "'platform.product_roles.synced.v1'"
            ")"
        )
    )
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text("ALTER TABLE role_changes DISABLE TRIGGER trg_role_changes_append_only")
        )
    op.execute(sa.text("DELETE FROM role_changes"))
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text("ALTER TABLE role_changes ENABLE TRIGGER trg_role_changes_append_only")
        )
    op.execute(sa.text("DELETE FROM role_assignments"))
    op.execute(sa.text("DELETE FROM role_permissions"))
    op.execute(sa.text("DELETE FROM roles"))
    op.alter_column("roles", "key", nullable=False)
    op.create_unique_constraint("uq_roles_key", "roles", ["key"])
    op.create_check_constraint(
        "ck_roles_permission_policy",
        "roles",
        "permission_policy IN ('explicit', 'all', 'all_scoped', 'read_scoped')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_roles_permission_policy", "roles", type_="check")
    op.drop_constraint("uq_roles_key", "roles", type_="unique")
    op.drop_column("roles", "permission_policy")
    op.drop_column("roles", "built_in")
    op.drop_column("roles", "key")
