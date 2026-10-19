"""Deterministic baseline and study-owner access owned by Platform."""

from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from aurinara_platform.models import Permission, Role, RoleAssignment, RolePermission, User

REGISTERED_STUDY_CREATOR_ROLE_ID = uuid5(
    NAMESPACE_URL, "aurinara-platform:role:registered-study-creator"
)
PLATFORM_STUDY_OWNER_ROLE_ID = uuid5(NAMESPACE_URL, "aurinara-platform:role:platform-study-owner")

AURINARA_STUDY_OWNER_ROLE_NAME = "Aurinara Study Owner"
EDC_STUDY_OWNER_ROLE_NAMES = (
    "Study Administrator",
    "Designer_Role",
    "Operational_Role",
    "CTMS_Operations_User",
    "Safety_Manager",
)

_PLATFORM_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "platform.study.create": ("system",),
    "platform.study.read": ("system", "study"),
    "platform.study.manage": ("system", "study"),
    "platform.membership.read": ("system", "study"),
    "platform.membership.manage": ("system", "study"),
    "platform.repository.read": ("system", "study"),
    "platform.repository.write": ("system", "study"),
}

_PLATFORM_ROLES: tuple[tuple[UUID, str, str, bool, tuple[str, ...]], ...] = (
    (
        REGISTERED_STUDY_CREATOR_ROLE_ID,
        "Registered Study Creator",
        "system",
        False,
        ("platform.study.create",),
    ),
    (
        PLATFORM_STUDY_OWNER_ROLE_ID,
        "Platform Study Owner",
        "study",
        True,
        (
            "platform.study.read",
            "platform.study.manage",
            "platform.membership.read",
            "platform.membership.manage",
            "platform.repository.read",
            "platform.repository.write",
        ),
    ),
)


def ensure_platform_access_defaults(session: Session) -> bool:
    """Install the reviewed Platform roles and their exact permission composition."""

    changed = False
    for code, scopes in _PLATFORM_PERMISSIONS.items():
        permission = session.get(Permission, code)
        if permission is None:
            permission = Permission(
                code=code,
                product="platform",
                description="Aurinara Platform access permission",
                module="platform",
                allowed_scope_levels=list(scopes),
            )
            session.add(permission)
            changed = True
        else:
            expected = {
                "product": "platform",
                "module": "platform",
                "allowed_scope_levels": list(scopes),
                "status": "active",
            }
            for field, value in expected.items():
                if getattr(permission, field) != value:
                    setattr(permission, field, value)
                    changed = True
    session.flush()

    for role_id, name, scope, assignable, permission_codes in _PLATFORM_ROLES:
        role = session.get(Role, role_id)
        if role is None:
            collision = session.scalar(
                select(Role).where(Role.product == "platform", Role.name == name)
            )
            if collision is not None:
                raise RuntimeError(f"Platform role name is already owned by {collision.id}: {name}")
            role = Role(
                id=role_id,
                product="platform",
                name=name,
                description="Built-in least-privilege Platform role",
                scope_level=scope,
                assignable=assignable,
                status="active",
            )
            session.add(role)
            changed = True
        else:
            expected_role = {
                "product": "platform",
                "name": name,
                "scope_level": scope,
                "assignable": assignable,
                "status": "active",
            }
            for field, value in expected_role.items():
                if getattr(role, field) != value:
                    setattr(role, field, value)
                    changed = True
        session.flush()
        existing = set(
            session.scalars(
                select(RolePermission.permission_code).where(RolePermission.role_id == role_id)
            )
        )
        required = set(permission_codes)
        if existing - required:
            session.execute(
                delete(RolePermission).where(
                    RolePermission.role_id == role_id,
                    RolePermission.permission_code.in_(existing - required),
                )
            )
            changed = True
        for code in sorted(required - existing):
            session.add(RolePermission(role_id=role_id, permission_code=code))
            changed = True
    session.flush()
    return changed


def ensure_registered_study_creator(session: Session, user: User) -> bool:
    """Grant the non-assignable create-only role to one human identity."""

    existing = session.scalar(
        select(RoleAssignment.id).where(
            RoleAssignment.user_id == user.id,
            RoleAssignment.role_id == REGISTERED_STUDY_CREATOR_ROLE_ID,
            RoleAssignment.study_id.is_(None),
            RoleAssignment.site_id.is_(None),
        )
    )
    if existing is not None:
        return False
    session.add(
        RoleAssignment(
            user_id=user.id,
            role_id=REGISTERED_STUDY_CREATOR_ROLE_ID,
            assigned_by=user.id,
        )
    )
    session.flush()
    return True


def study_owner_roles(session: Session) -> dict[str, list[Role]]:
    """Resolve every reviewed role required before a canonical study can be created."""

    platform_role = session.get(Role, PLATFORM_STUDY_OWNER_ROLE_ID)
    aurinara_role = session.scalar(
        select(Role).where(
            Role.product == "aurinara",
            Role.name == AURINARA_STUDY_OWNER_ROLE_NAME,
            Role.scope_level == "study",
            Role.status == "active",
        )
    )
    edc_roles = list(
        session.scalars(
            select(Role).where(
                Role.product == "aurinara-edc",
                Role.name.in_(EDC_STUDY_OWNER_ROLE_NAMES),
                Role.scope_level == "study",
                Role.status == "active",
            )
        )
    )
    edc_by_name = {role.name: role for role in edc_roles}
    if (
        platform_role is None
        or platform_role.status != "active"
        or platform_role.scope_level != "study"
        or aurinara_role is None
        or set(edc_by_name) != set(EDC_STUDY_OWNER_ROLE_NAMES)
    ):
        raise RuntimeError("reviewed study-owner roles are not fully registered")
    return {
        "platform": [platform_role],
        "aurinara": [aurinara_role],
        "aurinara-edc": [edc_by_name[name] for name in EDC_STUDY_OWNER_ROLE_NAMES],
    }
