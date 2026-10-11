from uuid import uuid4

from sqlalchemy.orm import Session

from aurinara_platform.config import Settings
from aurinara_platform.models import (
    Permission,
    PolicyState,
    Role,
    RoleAssignment,
    RolePermission,
    Study,
    User,
)
from aurinara_platform.schemas import AuthorizationDecisionRequest
from aurinara_platform.services import AuthorizationService


def _request(user: User, action: str, study_id=None, site_id=None):
    return AuthorizationDecisionRequest.model_validate(
        {
            "principal": {"issuer": user.issuer, "subject": user.subject},
            "action": action,
            "resource": {
                "product": "edc",
                "type": "form",
                "study_id": study_id,
                "site_id": site_id,
            },
        }
    )


def test_scoped_decision_and_revocation_are_default_deny(session: Session) -> None:
    user = User(issuer="https://issuer", subject="user-1", email="one@example.test")
    admin = User(issuer="https://issuer", subject="admin")
    session.add_all([user, admin])
    session.flush()
    study = Study(code="STUDY-1", display_name="Study One", created_by=admin.id)
    session.add(study)
    permission = Permission(code="edc.form.enter", product="edc")
    role = Role(product="edc", name="Data Entry")
    session.add_all([permission, role])
    session.flush()
    session.add(RolePermission(role_id=role.id, permission_code=permission.code))
    site_id = uuid4()
    assignment = RoleAssignment(
        user_id=user.id,
        role_id=role.id,
        study_id=study.id,
        site_id=site_id,
        assigned_by=admin.id,
    )
    session.add_all([assignment, PolicyState(id=1, version=8)])
    session.commit()

    service = AuthorizationService(
        Settings(environment="test", authorization_decision_ttl_seconds=60)
    )
    allowed = service.decide(session, _request(user, "edc.form.enter", study.id, site_id))
    wrong_site = service.decide(session, _request(user, "edc.form.enter", study.id, uuid4()))
    wrong_study = service.decide(session, _request(user, "edc.form.enter", uuid4(), site_id))
    unknown_action = service.decide(session, _request(user, "edc.form.sign", study.id, site_id))

    assert allowed.allowed is True
    assert allowed.policy_version == 8
    assert (
        allowed.expires_at - allowed.expires_at.replace(second=allowed.expires_at.second)
    ).seconds == 0
    assert wrong_site.allowed is False
    assert wrong_study.allowed is False
    assert unknown_action.allowed is False

    session.delete(assignment)
    session.commit()
    assert (
        service.decide(session, _request(user, "edc.form.enter", study.id, site_id)).allowed
        is False
    )


def test_inactive_user_is_denied_even_with_assignment(session: Session) -> None:
    user = User(issuer="https://issuer", subject="inactive", status="inactive")
    session.add_all([user, PolicyState(id=1, version=1)])
    session.commit()
    service = AuthorizationService(Settings(environment="test"))

    result = service.decide(session, _request(user, "edc.form.enter"))

    assert result.allowed is False
    assert result.reason_code == "account_inactive"
