"""Idempotently register local Cognito M2M clients and least-privilege roles."""

import argparse
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from aurinara_platform.config import get_settings
from aurinara_platform.database import create_session_factory
from aurinara_platform.models import (
    Permission,
    Role,
    RoleAssignment,
    RolePermission,
    User,
)


def _stable_id(kind: str, value: str):
    return uuid5(NAMESPACE_URL, f"aurinara-platform:{kind}:{value}")


def _register(
    session,
    *,
    issuer: str,
    client_id: str,
    product: str,
    assigned_by,
) -> None:
    user = session.scalar(select(User).where(User.issuer == issuer, User.subject == client_id))
    if user is None:
        user = User(
            id=_stable_id("service-user", client_id),
            issuer=issuer,
            subject=client_id,
            display_name=f"{product} service",
        )
        session.add(user)

    role_name = f"{product} Service"
    role = session.scalar(select(Role).where(Role.product == product, Role.name == role_name))
    if role is None:
        role = Role(
            id=_stable_id("service-role", product),
            product=product,
            name=role_name,
            description="Runtime service-to-service access",
        )
        session.add(role)
    session.flush()

    for code in (
        "platform.authorization.decide",
        f"{product}.object.upload",
        f"{product}.object.download",
        f"{product}.object.reference",
    ):
        permission = session.get(Permission, code)
        if permission is None:
            permission = Permission(code=code, product=code.split(".", 1)[0])
            session.add(permission)
        if session.get(RolePermission, (role.id, code)) is None:
            session.add(RolePermission(role_id=role.id, permission_code=code))

    assignment_id = _stable_id("service-assignment", f"{client_id}:{product}")
    if session.get(RoleAssignment, assignment_id) is None:
        session.add(
            RoleAssignment(
                id=assignment_id,
                user_id=user.id,
                role_id=role.id,
                assigned_by=assigned_by,
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aurinara-client-id", required=True)
    parser.add_argument("--edc-client-id", required=True)
    args = parser.parse_args()

    settings = get_settings()
    factory = create_session_factory(settings)
    with factory.begin() as session:
        actor = session.scalar(select(User).order_by(User.created_at))
        if actor is None:
            raise RuntimeError("Sign in to the platform once before bootstrapping service accounts")
        _register(
            session,
            issuer=settings.oidc_issuer,
            client_id=args.aurinara_client_id,
            product="aurinara",
            assigned_by=actor.id,
        )
        _register(
            session,
            issuer=settings.oidc_issuer,
            client_id=args.edc_client_id,
            product="aurinara-edc",
            assigned_by=actor.id,
        )
    print("service_accounts=ready")


if __name__ == "__main__":
    main()
