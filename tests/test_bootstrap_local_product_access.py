from sqlalchemy import select
from sqlalchemy.orm import Session

from aurinara_platform.config import Settings
from aurinara_platform.models import (
    Permission,
    PolicyState,
    Role,
    RoleAssignment,
    RolePermission,
    User,
)
from scripts.bootstrap_local_product_access import ROLE_SPECS, bootstrap


def test_bootstrap_grants_product_access_to_platform_administrator(session: Session) -> None:
    administrator = User(issuer="https://issuer", subject="admin")
    platform_role = Role(product="platform", name="Platform Administrator")
    permission = Permission(code="platform.study.create", product="platform")
    session.add_all([administrator, platform_role, permission, PolicyState(id=1, version=1)])
    session.flush()
    session.add(
        RolePermission(role_id=platform_role.id, permission_code=permission.code)
    )
    session.add(
        RoleAssignment(
            user_id=administrator.id,
            role_id=platform_role.id,
            assigned_by=administrator.id,
        )
    )
    session.commit()

    assert bootstrap(session, Settings(environment="development")) == (2, 2)
    session.commit()
    assert bootstrap(session, Settings(environment="development")) == (0, 0)

    assignments = list(
        session.scalars(
            select(RoleAssignment).where(RoleAssignment.user_id == administrator.id)
        )
    )
    assert len(assignments) == 3
    for product, role_name, permission_codes in ROLE_SPECS:
        role = session.scalar(
            select(Role).where(Role.product == product, Role.name == role_name)
        )
        assert role is not None
        actual = set(
            session.scalars(
                select(RolePermission.permission_code).where(
                    RolePermission.role_id == role.id
                )
            )
        )
        assert actual == set(permission_codes)
