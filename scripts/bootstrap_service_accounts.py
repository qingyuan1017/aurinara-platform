"""Idempotently register local Cognito M2M clients and least-privilege roles."""

import argparse
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from aurinara_platform.config import get_settings
from aurinara_platform.database import create_session_factory
from aurinara_platform.models import (
    Permission,
    PolicyState,
    Role,
    RoleAssignment,
    RolePermission,
    User,
)


def _stable_id(kind: str, value: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"aurinara-platform:{kind}:{value}")


def _register(
    session: Session,
    *,
    issuer: str,
    client_id: str,
    product: str,
) -> bool:
    changed = False
    user = session.scalar(select(User).where(User.issuer == issuer, User.subject == client_id))
    if user is None:
        user = session.get(User, _stable_id("service-user", client_id))
        if user is None:
            user = User(
                id=_stable_id("service-user", client_id),
                issuer=issuer,
                subject=client_id,
                display_name=f"{product} service",
            )
            session.add(user)
            changed = True
    expected_user_values = {
        "issuer": issuer,
        "subject": client_id,
        "display_name": f"{product} service",
        "status": "active",
    }
    for attribute, expected in expected_user_values.items():
        if getattr(user, attribute) != expected:
            setattr(user, attribute, expected)
            changed = True

    role_name = f"{product} Service"
    role = session.scalar(select(Role).where(Role.product == product, Role.name == role_name))
    if role is None:
        role = Role(
            id=_stable_id("service-role", product),
            key=f"service.{product}",
            product=product,
            name=role_name,
            description="Runtime service-to-service access",
            scope_level="system",
            assignable=False,
        )
        session.add(role)
        changed = True
    else:
        expected_role_values = {
            "scope_level": "system",
            "assignable": False,
            "status": "active",
        }
        for attribute, expected in expected_role_values.items():
            if getattr(role, attribute) != expected:
                setattr(role, attribute, expected)
                changed = True
    session.flush()

    object_permissions = {
        "aurinara": (
            "aurinara.artifacts.write",
            "aurinara.artifacts.read",
            "aurinara.permission.sync",
        ),
        "aurinara-edc": ("edc.file.upload",),
    }
    permission_codes = ["platform.authorization.decide", *object_permissions[product]]
    if product == "aurinara-edc":
        permission_codes.extend(("edc.permission.sync", "edc.site.sync"))
    for code in permission_codes:
        permission_product = "platform" if code.startswith("platform.") else product
        permission = session.get(Permission, code)
        if permission is None:
            permission = Permission(
                code=code,
                product=permission_product,
                module="platform",
                allowed_scope_levels=["system"],
            )
            session.add(permission)
            changed = True
        elif permission.product != permission_product:
            permission.product = permission_product
            changed = True
        if session.get(RolePermission, (role.id, code)) is None:
            session.add(RolePermission(role_id=role.id, permission_code=code))
            changed = True

    assignment_id = _stable_id("service-assignment", f"{client_id}:{product}")
    if session.get(RoleAssignment, assignment_id) is None:
        session.add(
            RoleAssignment(
                id=assignment_id,
                user_id=user.id,
                role_id=role.id,
                assigned_by=user.id,
            )
        )
        changed = True
    return changed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aurinara-client-id", required=True)
    parser.add_argument("--edc-client-id", required=True)
    args = parser.parse_args()

    settings = get_settings()
    factory = create_session_factory(settings)
    with factory.begin() as session:
        changed = _register(
            session,
            issuer=settings.oidc_issuer,
            client_id=args.aurinara_client_id,
            product="aurinara",
        )
        changed = (
            _register(
                session,
                issuer=settings.oidc_issuer,
                client_id=args.edc_client_id,
                product="aurinara-edc",
            )
            or changed
        )
        policy = session.get(PolicyState, 1)
        if policy is None:
            raise RuntimeError("Platform policy state is not initialized")
        if changed:
            policy.version += 1
    print("service_accounts=ready")


if __name__ == "__main__":
    main()
