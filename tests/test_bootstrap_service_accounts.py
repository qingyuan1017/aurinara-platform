from sqlalchemy import select
from sqlalchemy.orm import Session

from aurinara_platform.models import Permission, Role, RoleAssignment, RolePermission, User
from scripts.bootstrap_service_accounts import _register, _stable_id


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
    assert permission_products["aurinara.artifacts.write"] == "aurinara"
    assert permission_products["aurinara.artifacts.read"] == "aurinara"
    assert permission_products["aurinara.permission.sync"] == "aurinara"
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
        "edc.site.sync",
    }.issubset(edc_permissions)


def test_service_account_bootstrap_reconciles_issuer_for_stable_user(
    session: Session,
) -> None:
    client_id = "aurinara-client"
    user = User(
        id=_stable_id("service-user", client_id),
        issuer="https://old-issuer.example",
        subject=client_id,
        display_name="aurinara service",
    )
    session.add(user)
    session.flush()

    assert _register(
        session,
        issuer="https://current-issuer.example",
        client_id=client_id,
        product="aurinara",
    )
    session.flush()

    assert user.issuer == "https://current-issuer.example"
    assert list(session.scalars(select(User.id))) == [user.id]
    role = session.scalar(select(Role).where(Role.key == "service.aurinara"))
    assert role is not None
