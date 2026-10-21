"""Register one explicitly selected Cognito identity as the initial administrator.

Production forbids implicit first-user bootstrap. This command is the reviewed,
idempotent operational path for establishing the first human administrator. It
requires the immutable Cognito subject and emits the normal EDC assignment event
so consumer authorization remains Platform-managed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aurinara_platform.config import get_settings
from aurinara_platform.database import create_session_factory
from aurinara_platform.default_access import (
    SYSTEM_ADMINISTRATOR_ROLE_ID,
    ensure_platform_access_defaults,
)
from aurinara_platform.models import PolicyState, Role, RoleAssignment, User, UserStatus, utc_now
from aurinara_platform.services import _emit, _emit_platform_role_change

PLATFORM_ADMIN_ROLE_ID = SYSTEM_ADMINISTRATOR_ROLE_ID


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    user_id: UUID
    changed: bool


def _stable_assignment_id(user_id: UUID, role_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"aurinara-platform:initial-assignment:{user_id}:{role_id}")


def bootstrap_initial_administrator(
    session: Session,
    *,
    issuer: str,
    subject: str,
    email: str,
    display_name: str,
) -> BootstrapResult:
    """Create the selected user and system assignments exactly once."""

    issuer = issuer.strip()
    subject = subject.strip()
    email = email.strip()
    display_name = display_name.strip()
    if not issuer or not subject or not email or not display_name:
        raise ValueError("issuer, subject, email, and display name are required")

    changed = False
    user = session.scalar(select(User).where(User.issuer == issuer, User.subject == subject))
    if user is None:
        human_count = session.scalar(
            select(func.count()).select_from(User).where(User.email.is_not(None))
        )
        if human_count:
            raise RuntimeError("an initial human administrator is already registered")
        user = User(
            issuer=issuer,
            subject=subject,
            email=email,
            display_name=display_name,
            status=UserStatus.active,
        )
        session.add(user)
        session.flush()
        changed = True
    elif user.status != UserStatus.active:
        raise RuntimeError("the selected administrator identity is inactive")

    changed = ensure_platform_access_defaults(session) or changed
    session.flush()
    assignment_id = _stable_assignment_id(user.id, SYSTEM_ADMINISTRATOR_ROLE_ID)
    if session.get(RoleAssignment, assignment_id) is None:
        session.add(
            RoleAssignment(
                id=assignment_id,
                user_id=user.id,
                role_id=SYSTEM_ADMINISTRATOR_ROLE_ID,
                assigned_by=user.id,
            )
        )
        changed = True

    state = session.get(PolicyState, 1)
    if state is None:
        raise RuntimeError("Platform policy state is not initialized")
    if changed:
        state.version += 1
        state.updated_at = utc_now()
        session.flush()
        role = session.get(Role, SYSTEM_ADMINISTRATOR_ROLE_ID)
        if role is None:
            raise RuntimeError("System Administrator role is not initialized")
        _emit_platform_role_change(session, role, state.version)
        _emit(
            session,
            "platform.assignment.changed.v1",
            "user",
            user.id,
            state.version,
            {
                "user_id": str(user.id),
                "issuer": user.issuer,
                "subject": user.subject,
                "product": "platform",
                "role_ids": [str(SYSTEM_ADMINISTRATOR_ROLE_ID)],
                "assigned_by": str(user.id),
                "policy_version": state.version,
            },
        )
        session.flush()
    return BootstrapResult(user_id=user.id, changed=changed)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--issuer")
    parser.add_argument("--confirm-production", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    if settings.environment == "production" and not args.confirm_production:
        raise SystemExit("--confirm-production is required in production")
    factory = create_session_factory(settings)
    with factory.begin() as session:
        result = bootstrap_initial_administrator(
            session,
            issuer=args.issuer or settings.oidc_issuer,
            subject=args.subject,
            email=args.email,
            display_name=args.display_name,
        )
    outcome = "created" if result.changed else "ready"
    print(f"initial_administrator={outcome} user_id={result.user_id}")


if __name__ == "__main__":
    main()
