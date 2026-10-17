"""Grant development platform administrators product access for runtime integration.

This helper is intentionally development-only. Production role definitions and
assignments continue to arrive through reviewed migration snapshots.
"""

from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select

from aurinara_platform.config import Settings, get_settings
from aurinara_platform.database import create_session_factory
from aurinara_platform.models import (
    Permission,
    PolicyState,
    Role,
    RoleAssignment,
    RolePermission,
    User,
    utc_now,
)
from aurinara_platform.services import _emit

EDC_SYSTEM_ADMIN_ROLE_ID = UUID("e208baf5-110f-5708-a483-d643f9719bfb")

ROLE_SPECS = (
    (
        "aurinara",
        "Development Aurinara Administrator",
        (
            "artifact.study.read",
            "artifact.study.write",
            "artifact.object.upload",
            "artifact.object.download",
            "artifact.object.reference",
            "artifact.execution.run",
            "artifact.validation.run",
            "artifact.reconciliation.run",
            "artifact.package.build",
        ),
    ),
    (
        "aurinara-edc",
        "EDC System Administrator",
        (),
    ),
)


def _stable_id(kind: str, value: str):
    return uuid5(NAMESPACE_URL, f"aurinara-platform:{kind}:{value}")


def bootstrap(session, settings: Settings) -> tuple[int, int]:
    """Create deterministic local roles and assign them to platform administrators."""

    if settings.environment != "development":
        raise RuntimeError("local product access bootstrap is development-only")

    administrators = list(
        session.scalars(
            select(User)
            .join(RoleAssignment, RoleAssignment.user_id == User.id)
            .join(RolePermission, RolePermission.role_id == RoleAssignment.role_id)
            .where(RolePermission.permission_code == "platform.study.create")
            .distinct()
        )
    )
    if not administrators:
        raise RuntimeError("no platform administrator is registered")

    roles: list[Role] = []
    created_roles = 0
    for product, name, permission_codes in ROLE_SPECS:
        role = session.scalar(select(Role).where(Role.product == product, Role.name == name))
        if role is None:
            role = Role(
                id=(
                    EDC_SYSTEM_ADMIN_ROLE_ID
                    if product == "aurinara-edc"
                    else _stable_id("development-role", product)
                ),
                product=product,
                name=name,
                description="Development-only runtime integration access",
            )
            session.add(role)
            session.flush()
            created_roles += 1
        roles.append(role)
        for code in permission_codes:
            permission = session.get(Permission, code)
            if permission is None:
                permission = Permission(
                    code=code,
                    product=product,
                    module="platform" if product == "platform" else product,
                    allowed_scope_levels=["system", "study", "site"],
                )
                session.add(permission)
            elif code.startswith("artifact."):
                permission.product = "aurinara"
            if session.get(RolePermission, (role.id, code)) is None:
                session.add(RolePermission(role_id=role.id, permission_code=code))

    session.flush()
    created_assignments = 0
    for administrator in administrators:
        for role in roles:
            assignment_id = _stable_id("development-assignment", f"{administrator.id}:{role.id}")
            if session.get(RoleAssignment, assignment_id) is None:
                session.add(
                    RoleAssignment(
                        id=assignment_id,
                        user_id=administrator.id,
                        role_id=role.id,
                        assigned_by=administrator.id,
                    )
                )
                created_assignments += 1

    state = session.get(PolicyState, 1)
    if state is None:
        state = PolicyState(id=1, version=1)
        session.add(state)
    if created_roles or created_assignments:
        state.version += 1
        state.updated_at = utc_now()
    session.flush()
    for administrator in administrators:
        edc_roles = [role for role in roles if role.product == "aurinara-edc"]
        _emit(
            session,
            "platform.assignment.changed.v1",
            "user",
            administrator.id,
            state.version,
            {
                "user_id": str(administrator.id),
                "issuer": administrator.issuer,
                "subject": administrator.subject,
                "product": "aurinara-edc",
                "role_ids": [str(role.id) for role in edc_roles],
                "assigned_by": str(administrator.id),
                "policy_version": state.version,
            },
        )
    session.flush()
    return created_roles, created_assignments


def main() -> None:
    settings = get_settings()
    factory = create_session_factory(settings)
    with factory.begin() as session:
        roles, assignments = bootstrap(session, settings)
    print(f"roles_created={roles} assignments_created={assignments}")


if __name__ == "__main__":
    main()
