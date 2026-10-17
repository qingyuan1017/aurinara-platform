"""Move product role composition into the Platform control plane.

Revision ID: 0006_platform_managed_roles
Revises: 0005_canonical_sites
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_platform_managed_roles"
down_revision: str | None = "0005_canonical_sites"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("permissions", sa.Column("module", sa.String(length=64), nullable=True))
    op.add_column(
        "permissions",
        sa.Column(
            "allowed_scope_levels",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text('\'["system", "study", "site"]\'::jsonb'),
        ),
    )
    op.add_column(
        "permissions",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
    )
    op.create_check_constraint(
        "ck_permissions_status", "permissions", "status IN ('active', 'inactive')"
    )
    op.execute(sa.text("UPDATE permissions SET module = split_part(code, '.', 1)"))
    op.execute(
        sa.text(
            "UPDATE permissions SET module = 'platform', "
            "allowed_scope_levels = '[\"system\"]'::jsonb "
            "WHERE code IN ('edc.permission.sync', 'edc.role.sync', 'edc.site.sync')"
        )
    )

    op.add_column("roles", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    op.add_column(
        "roles",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "role_changes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "role_id", sa.Uuid(), sa.ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("previous_value", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("new_value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("policy_version", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_role_changes_role_created", "role_changes", ["role_id", "created_at"])
    op.execute(
        sa.text(
            "CREATE FUNCTION prevent_role_change_mutation() RETURNS trigger AS $$ "
            "BEGIN RAISE EXCEPTION 'role_changes is append-only'; END; "
            "$$ LANGUAGE plpgsql"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_role_changes_append_only "
            "BEFORE UPDATE OR DELETE ON role_changes "
            "FOR EACH ROW EXECUTE FUNCTION prevent_role_change_mutation()"
        )
    )

    connection = op.get_bind()
    connection.execute(
        sa.text(
            "INSERT INTO permissions "
            "(code, product, description, module, allowed_scope_levels, status) "
            "VALUES ('edc.permission.sync', 'aurinara-edc', "
            "'Register the Aurinara EDC permission catalog', 'platform', "
            "'[\"system\"]'::jsonb, 'active') ON CONFLICT (code) DO NOTHING"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_code) "
            "SELECT id, 'edc.permission.sync' FROM roles "
            "WHERE product = 'aurinara-edc' AND name = 'aurinara-edc Service' "
            "ON CONFLICT DO NOTHING"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO permissions "
            "(code, product, description, module, allowed_scope_levels, status) "
            "VALUES ('platform.role.manage', 'platform', "
            "'Create and maintain product roles', 'platform', "
            "'[\"system\"]'::jsonb, 'active') ON CONFLICT (code) DO NOTHING"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_code) "
            "SELECT id, 'platform.role.manage' FROM roles "
            "WHERE product = 'platform' AND name = 'Platform Administrator' "
            "ON CONFLICT DO NOTHING"
        )
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "DELETE FROM role_permissions "
            "WHERE permission_code IN ('edc.permission.sync', 'platform.role.manage')"
        )
    )
    connection.execute(
        sa.text(
            "DELETE FROM permissions WHERE code IN ('edc.permission.sync', 'platform.role.manage')"
        )
    )
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_role_changes_append_only ON role_changes"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS prevent_role_change_mutation()"))
    op.drop_index("ix_role_changes_role_created", table_name="role_changes")
    op.drop_table("role_changes")
    op.drop_column("roles", "updated_at")
    op.drop_column("roles", "revision")
    op.drop_constraint("ck_permissions_status", "permissions", type_="check")
    op.drop_column("permissions", "status")
    op.drop_column("permissions", "allowed_scope_levels")
    op.drop_column("permissions", "module")
