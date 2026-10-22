"""Platform-owned permissions and built-in roles.

Products register permission vocabularies. Platform alone defines roles and
assigns them. Platform-wide policy roles evaluate registered permission scope;
product administrator roles explicitly include their product's active
non-service permissions and are reconciled after each permission sync.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from aurinara_platform.models import Permission, Role, RoleAssignment, RolePermission, User


def _role_id(key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"aurinara-platform:role:{key}")


SYSTEM_ADMINISTRATOR_ROLE_ID = _role_id("system_administrator")
REGISTERED_STUDY_CREATOR_ROLE_ID = _role_id("registered_study_creator")
STUDY_ADMINISTRATOR_ROLE_ID = _role_id("study_administrator")
STUDY_VIEWER_ROLE_ID = _role_id("study_viewer")

# Compatibility import for the small number of callers that protect the last
# study administrator. It is the same role, not a separate owner concept.
PLATFORM_STUDY_OWNER_ROLE_ID = STUDY_ADMINISTRATOR_ROLE_ID

_PLATFORM_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "platform.user.manage": ("system",),
    "platform.study.create": ("system",),
    "platform.study.read": ("system", "study"),
    "platform.study.manage": ("system", "study"),
    "platform.membership.read": ("system", "study"),
    "platform.membership.manage": ("system", "study"),
    "platform.role.manage": ("system",),
    "platform.authorization.decide": ("system",),
    "platform.object.upload": ("system", "study"),
    "platform.object.download": ("system", "study"),
    "platform.object.reference": ("system", "study"),
    "platform.repository.read": ("system", "study"),
    "platform.repository.write": ("system", "study"),
}


@dataclass(frozen=True, slots=True)
class BuiltInRole:
    key: str
    name: str
    scope_level: str
    permission_policy: str
    assignable: bool = True
    product: str = "platform"
    description: str = "Platform-managed built-in role"
    permission_codes: tuple[str, ...] = ()

    @property
    def id(self) -> UUID:
        return _role_id(self.key)


_CORE_ROLES = (
    BuiltInRole(
        "system_administrator",
        "System Administrator",
        "system",
        "all",
        description="Unrestricted administration across Platform and all products",
    ),
    BuiltInRole(
        "registered_study_creator",
        "Study Creator",
        "system",
        "explicit",
        assignable=False,
        description="Create canonical studies",
        permission_codes=("platform.study.create",),
    ),
    BuiltInRole(
        "study_administrator",
        "Study Administrator",
        "study",
        "all_scoped",
        description="All study and site capabilities within an assigned study",
    ),
    BuiltInRole(
        "study_viewer",
        "Study Viewer",
        "study",
        "read_scoped",
        description="All read capabilities within an assigned study",
    ),
)


def _starts_with(*prefixes: str) -> Callable[[str], bool]:
    return lambda code: code.startswith(prefixes)


def _codes(*values: str) -> Callable[[str], bool]:
    allowed = frozenset(values)
    return lambda code: code in allowed


# Products own these codes; Platform owns these role compositions.
_PRODUCT_ROLES: dict[str, tuple[tuple[BuiltInRole, Callable[[str], bool]], ...]] = {
    "aurinara": (
        (
            BuiltInRole(
                "aurinara.administrator",
                "Aurinara Administrator",
                "system",
                "all",
                product="aurinara",
                description="Full Aurinara access across all studies",
            ),
            lambda code: code != "aurinara.permission.sync",
        ),
        (
            BuiltInRole(
                "aurinara.data_manager",
                "Aurinara Data Manager",
                "study",
                "explicit",
                product="aurinara",
            ),
            _starts_with(
                "aurinara.raw.",
                "aurinara.reference.",
                "aurinara.validation.",
                "aurinara.reconciliation.",
            ),
        ),
        (
            BuiltInRole(
                "aurinara.sdtm_programmer",
                "SDTM Programmer",
                "study",
                "explicit",
                product="aurinara",
            ),
            _starts_with("aurinara.sdtm.", "aurinara.validation."),
        ),
        (
            BuiltInRole(
                "aurinara.adam_programmer",
                "ADaM Programmer",
                "study",
                "explicit",
                product="aurinara",
            ),
            _starts_with("aurinara.adam.", "aurinara.validation."),
        ),
        (
            BuiltInRole(
                "aurinara.tlf_programmer", "TLF Programmer", "study", "explicit", product="aurinara"
            ),
            _starts_with("aurinara.tlf.", "aurinara.execution."),
        ),
        (
            BuiltInRole(
                "aurinara.reviewer", "Aurinara Reviewer", "study", "explicit", product="aurinara"
            ),
            lambda code: code.startswith("aurinara.review.") or code.endswith(".read"),
        ),
        (
            BuiltInRole(
                "aurinara.release_manager",
                "Release Manager",
                "study",
                "explicit",
                product="aurinara",
            ),
            _starts_with("aurinara.artifacts.", "aurinara.execution.", "aurinara.packaging."),
        ),
    ),
    "aurinara-edc": (
        (
            BuiltInRole(
                "edc.administrator",
                "Aurinara EDC Administrator",
                "system",
                "all",
                product="aurinara-edc",
                description="Full EDC, CTMS, and Safety access across all studies",
            ),
            lambda code: code not in {"edc.permission.sync", "edc.site.sync"},
        ),
        (
            BuiltInRole(
                "edc.designer", "EDC Designer", "study", "explicit", product="aurinara-edc"
            ),
            _starts_with("edc.designer."),
        ),
        (
            BuiltInRole(
                "edc.data_manager", "EDC Data Manager", "study", "explicit", product="aurinara-edc"
            ),
            _starts_with("edc.subject.", "edc.query.", "edc.review.", "edc.lock.", "edc.data."),
        ),
        (
            BuiltInRole("edc.cra", "CRA", "study", "explicit", product="aurinara-edc"),
            _codes(
                "edc.study.read",
                "edc.subject.read",
                "edc.form.read",
                "edc.sdv.manage",
                "edc.audit.read",
            ),
        ),
        (
            BuiltInRole(
                "edc.investigator", "Investigator", "site", "explicit", product="aurinara-edc"
            ),
            _codes(
                "edc.subject.read",
                "edc.form.read",
                "edc.form.enter",
                "edc.form.submit",
                "edc.query.respond",
                "edc.signature.sign",
            ),
        ),
        (
            BuiltInRole(
                "ctms.manager", "CTMS Manager", "study", "explicit", product="aurinara-edc"
            ),
            _starts_with("ctms."),
        ),
        (
            BuiltInRole(
                "pv.safety_manager", "Safety Manager", "study", "explicit", product="aurinara-edc"
            ),
            _starts_with("pv."),
        ),
        (
            BuiltInRole(
                "suite.sponsor_viewer",
                "Sponsor Viewer",
                "study",
                "explicit",
                product="aurinara-edc",
            ),
            lambda code: code.endswith(".read"),
        ),
    ),
}


def _ensure_role(
    session: Session, definition: BuiltInRole, permission_codes: tuple[str, ...]
) -> bool:
    changed = False
    role = session.get(Role, definition.id)
    if role is None:
        role = session.scalar(select(Role).where(Role.key == definition.key))
    if role is None:
        role = Role(
            id=definition.id,
            key=definition.key,
            product=definition.product,
            name=definition.name,
        )
        session.add(role)
        changed = True
    expected = {
        "key": definition.key,
        "product": definition.product,
        "name": definition.name,
        "description": definition.description,
        "scope_level": definition.scope_level,
        "assignable": definition.assignable,
        "built_in": True,
        "permission_policy": definition.permission_policy,
        "status": "active",
        "source_role_id": None,
    }
    for field, value in expected.items():
        if getattr(role, field) != value:
            setattr(role, field, value)
            changed = True
    session.flush()
    existing = set(
        session.scalars(
            select(RolePermission.permission_code).where(RolePermission.role_id == role.id)
        )
    )
    required = set(permission_codes)
    if existing - required:
        session.execute(
            delete(RolePermission).where(
                RolePermission.role_id == role.id,
                RolePermission.permission_code.in_(existing - required),
            )
        )
        changed = True
    for code in sorted(required - existing):
        session.add(RolePermission(role_id=role.id, permission_code=code))
        changed = True
    return changed


def ensure_platform_access_defaults(session: Session) -> bool:
    """Install Platform permissions and core default roles."""

    changed = False
    for code, scopes in _PLATFORM_PERMISSIONS.items():
        permission = session.get(Permission, code)
        if permission is None:
            permission = Permission(code=code, product="platform")
            session.add(permission)
            changed = True
        expected = {
            "product": "platform",
            "description": "Aurinara Platform access permission",
            "module": "platform",
            "allowed_scope_levels": list(scopes),
            "status": "active",
        }
        for field, value in expected.items():
            if getattr(permission, field) != value:
                setattr(permission, field, value)
                changed = True
    session.flush()
    for definition in _CORE_ROLES:
        if definition.permission_policy == "explicit":
            changed = _ensure_role(session, definition, definition.permission_codes) or changed
    changed = ensure_policy_role_defaults(session) or changed
    session.flush()
    return changed


def ensure_policy_role_defaults(session: Session) -> bool:
    """Materialize effective policy permissions for projection and display."""

    permissions = tuple(session.scalars(select(Permission).where(Permission.status == "active")))
    compositions = {
        "system_administrator": tuple(sorted(permission.code for permission in permissions)),
        "study_administrator": tuple(
            sorted(
                permission.code
                for permission in permissions
                if {"study", "site"}.intersection(permission.allowed_scope_levels)
            )
        ),
        "study_viewer": tuple(
            sorted(
                permission.code
                for permission in permissions
                if permission.code.endswith(".read")
                and {"study", "site"}.intersection(permission.allowed_scope_levels)
            )
        ),
    }
    changed = False
    for definition in _CORE_ROLES:
        if definition.key in compositions:
            changed = _ensure_role(session, definition, compositions[definition.key]) or changed
    return changed


def ensure_product_role_defaults(session: Session, product: str) -> bool:
    """Reconcile Platform-owned functional roles after product permission sync."""

    active_permissions = tuple(
        session.scalars(
            select(Permission).where(Permission.product == product, Permission.status == "active")
        )
    )
    changed = ensure_policy_role_defaults(session)
    for definition, predicate in _PRODUCT_ROLES.get(product, ()):
        selected = tuple(
            sorted(
                permission.code
                for permission in active_permissions
                if predicate(permission.code)
                and (
                    definition.permission_policy == "all"
                    or definition.scope_level in permission.allowed_scope_levels
                )
            )
        )
        changed = _ensure_role(session, definition, selected) or changed
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
    """Return the single cross-product Study Administrator role."""

    role = session.get(Role, STUDY_ADMINISTRATOR_ROLE_ID)
    if role is None or role.status != "active" or role.scope_level != "study":
        raise RuntimeError("Study Administrator is not registered")
    return {"platform": [role]}
