"""Add the reviewed Aurinara EDC system administrator product role.

Revision ID: 0003_edc_system_admin
Revises: 0002_study_sponsor
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "0003_edc_system_admin"
down_revision: str | None = "0002_study_sponsor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EDC_SYSTEM_ADMIN_ROLE_ID = UUID("e208baf5-110f-5708-a483-d643f9719bfb")
EDC_SYSTEM_ADMIN_PERMISSIONS = (
    "edc.audit.read",
    "ctms.conflict_management",
    "ctms.coordination_replay",
    "ctms.enrollment_management",
    "ctms.monitoring_activity_management",
    "ctms.operational_data_read",
    "ctms.operational_site_management",
    "ctms.operational_study_management",
    "edc.data.export",
    "edc.designer.arm.configure",
    "edc.designer.arm.read",
    "edc.designer.codelist.configure",
    "edc.designer.codelist.read",
    "edc.designer.coding_setting.configure",
    "edc.designer.crf_access.configure",
    "edc.designer.editcheck.configure",
    "edc.designer.editcheck.test",
    "edc.designer.field.configure",
    "edc.designer.form.configure",
    "edc.designer.form.read",
    "edc.designer.raw_data_variable.configure",
    "edc.designer.soa.configure",
    "edc.designer.soa.read",
    "edc.designer.study_version.amend",
    "edc.designer.study_version.create",
    "edc.designer.study_version.read",
    "edc.designer.subject_phase.configure",
    "edc.designer.subject_phase.read",
    "edc.designer.version.publish",
    "edc.designer.visit.configure",
    "edc.designer.visit.read",
    "edc.editcheck.configure",
    "edc.file.upload",
    "edc.form.configure",
    "edc.form.enter",
    "edc.form.read",
    "edc.form.submit",
    "edc.lock.manage",
    "edc.query.cancel",
    "edc.query.close",
    "edc.query.create",
    "edc.query.reopen",
    "edc.query.respond",
    "edc.review.manage",
    "edc.role.create",
    "edc.role.list",
    "edc.role.update",
    "edc.safety_assessment.record",
    "edc.safety_audit.read",
    "edc.safety_case.enter",
    "edc.safety_case.lifecycle",
    "edc.safety_case.read",
    "edc.safety_coding.assign",
    "edc.safety_export.create",
    "edc.safety_narrative.write",
    "edc.safety_reconciliation.run",
    "edc.safety_report.manage",
    "edc.sdv.manage",
    "edc.signature.sign",
    "edc.site.manage",
    "edc.site.read",
    "edc.study.configure",
    "edc.study.create",
    "edc.study.read",
    "edc.subject.create",
    "edc.subject.read",
    "edc.subject.update",
    "edc.user.assign",
    "edc.user.create",
    "edc.user.deactivate",
    "edc.user.list",
    "edc.user.update",
    "edc.version.publish",
)


def upgrade() -> None:
    connection = op.get_bind()
    permissions = sa.table(
        "permissions",
        sa.column("code", sa.String),
        sa.column("product", sa.String),
        sa.column("description", sa.Text),
    )
    roles = sa.table(
        "roles",
        sa.column("id", sa.Uuid),
        sa.column("product", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_id", sa.Uuid),
        sa.column("permission_code", sa.String),
    )
    for code in EDC_SYSTEM_ADMIN_PERMISSIONS:
        exists = connection.scalar(sa.select(permissions.c.code).where(permissions.c.code == code))
        if exists is None:
            connection.execute(
                permissions.insert().values(
                    code=code,
                    product="aurinara-edc",
                    description="Aurinara EDC product permission",
                )
            )
    role_exists = connection.scalar(
        sa.select(roles.c.id).where(roles.c.id == EDC_SYSTEM_ADMIN_ROLE_ID)
    )
    if role_exists is None:
        connection.execute(
            roles.insert().values(
                id=EDC_SYSTEM_ADMIN_ROLE_ID,
                product="aurinara-edc",
                name="EDC System Administrator",
                description="Full system-level administration of Aurinara EDC, CTMS, and Safety",
                created_at=sa.func.now(),
            )
        )
    for code in EDC_SYSTEM_ADMIN_PERMISSIONS:
        exists = connection.scalar(
            sa.select(role_permissions.c.role_id).where(
                role_permissions.c.role_id == EDC_SYSTEM_ADMIN_ROLE_ID,
                role_permissions.c.permission_code == code,
            )
        )
        if exists is None:
            connection.execute(
                role_permissions.insert().values(
                    role_id=EDC_SYSTEM_ADMIN_ROLE_ID,
                    permission_code=code,
                )
            )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("DELETE FROM role_assignments WHERE role_id = :role_id"),
        {"role_id": EDC_SYSTEM_ADMIN_ROLE_ID},
    )
    connection.execute(
        sa.text("DELETE FROM role_permissions WHERE role_id = :role_id"),
        {"role_id": EDC_SYSTEM_ADMIN_ROLE_ID},
    )
    connection.execute(
        sa.text("DELETE FROM roles WHERE id = :role_id"),
        {"role_id": EDC_SYSTEM_ADMIN_ROLE_ID},
    )
