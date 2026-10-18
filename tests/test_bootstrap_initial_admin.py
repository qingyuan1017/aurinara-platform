import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aurinara_platform.models import (
    OutboxEvent,
    PolicyState,
    Role,
    RoleAssignment,
    User,
)
from scripts.bootstrap_initial_admin import (
    EDC_SYSTEM_ADMIN_ROLE_ID,
    PLATFORM_ADMIN_PERMISSIONS,
    bootstrap_initial_administrator,
)


def test_bootstrap_initial_administrator_is_explicit_and_idempotent(session: Session) -> None:
    session.add_all(
        [
            PolicyState(id=1, version=1),
            Role(
                id=EDC_SYSTEM_ADMIN_ROLE_ID,
                product="aurinara-edc",
                name="EDC System Administrator",
                scope_level="system",
            ),
        ]
    )
    session.commit()

    command = {
        "issuer": "https://issuer.example",
        "subject": "immutable-subject",
        "email": "administrator@example.test",
        "display_name": "Initial Administrator",
    }
    first = bootstrap_initial_administrator(session, **command)
    session.commit()
    second = bootstrap_initial_administrator(session, **command)
    session.commit()

    assert first.changed is True
    assert second == type(second)(user_id=first.user_id, changed=False)
    user = session.scalar(select(User).where(User.id == first.user_id))
    assert user is not None
    roles = list(
        session.scalars(
            select(Role)
            .join(RoleAssignment, RoleAssignment.role_id == Role.id)
            .where(RoleAssignment.user_id == user.id)
        )
    )
    assert {(role.product, role.name) for role in roles} == {
        ("platform", "Platform Administrator"),
        ("aurinara-edc", "EDC System Administrator"),
    }
    platform_role = next(role for role in roles if role.product == "platform")
    assert set(platform_role.permission_codes) == set(PLATFORM_ADMIN_PERMISSIONS)
    assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
    event = session.scalar(select(OutboxEvent))
    assert event is not None
    assert event.event_type == "platform.assignment.changed.v1"
    assert event.payload["subject"] == "immutable-subject"


def test_bootstrap_refuses_a_second_human_identity(session: Session) -> None:
    session.add_all(
        [
            PolicyState(id=1, version=1),
            Role(
                id=EDC_SYSTEM_ADMIN_ROLE_ID,
                product="aurinara-edc",
                name="EDC System Administrator",
                scope_level="system",
            ),
            User(
                issuer="https://issuer.example",
                subject="existing-subject",
                email="existing@example.test",
            ),
        ]
    )
    session.commit()

    with pytest.raises(
        RuntimeError, match="an initial human administrator is already registered"
    ):
        bootstrap_initial_administrator(
            session,
            issuer="https://issuer.example",
            subject="different-subject",
            email="different@example.test",
            display_name="Different Administrator",
        )
