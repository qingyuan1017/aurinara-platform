from sqlalchemy import select
from sqlalchemy.orm import Session

from aurinara_platform.models import Permission, Role, RoleAssignment, RolePermission, User
from scripts.bootstrap_service_accounts import _register


def test_service_account_bootstrap_is_idempotent(session: Session) -> None:
    issuer = "https://issuer.example"

    first_changes = _register(
        session,
        issuer=issuer,
        client_id="aurinara-client",
        product="aurinara",
    ) | _register(
        session,
        issuer=issuer,
        client_id="edc-client",
        product="aurinara-edc",
    )
    session.flush()
    second_changes = _register(
        session,
        issuer=issuer,
        client_id="aurinara-client",
        product="aurinara",
    ) | _register(
        session,
        issuer=issuer,
        client_id="edc-client",
        product="aurinara-edc",
    )
    session.flush()

    assert first_changes is True
    assert second_changes is False

    users = list(session.scalars(select(User).order_by(User.subject)))
    roles = list(session.scalars(select(Role).order_by(Role.product)))
    assignments = list(session.scalars(select(RoleAssignment)))
    assert [user.subject for user in users] == ["aurinara-client", "edc-client"]
    assert [(role.product, role.assignable) for role in roles] == [
        ("aurinara", False),
        ("aurinara-edc", False),
    ]
    assert len(assignments) == 2
    assert all(assignment.assigned_by == assignment.user_id for assignment in assignments)

    permission_products = {
        code: product
        for code, product in session.execute(select(Permission.code, Permission.product))
    }
    assert permission_products["platform.authorization.decide"] == "platform"
    assert permission_products["artifact.object.upload"] == "aurinara"
    assert permission_products["artifact.permission.sync"] == "aurinara"
    assert permission_products["artifact.role.sync"] == "aurinara"
    assert permission_products["edc.file.upload"] == "aurinara-edc"

    edc_role = next(role for role in roles if role.product == "aurinara-edc")
    edc_permissions = set(
        session.scalars(
            select(RolePermission.permission_code).where(RolePermission.role_id == edc_role.id)
        )
    )
    assert {
        "platform.authorization.decide",
        "edc.permission.sync",
        "edc.role.sync",
        "edc.site.sync",
    }.issubset(edc_permissions)
