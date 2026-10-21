from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from aurinara_platform.config import Settings
from aurinara_platform.default_access import (
    STUDY_ADMINISTRATOR_ROLE_ID,
    STUDY_VIEWER_ROLE_ID,
    SYSTEM_ADMINISTRATOR_ROLE_ID,
    ensure_platform_access_defaults,
    ensure_product_role_defaults,
)
from aurinara_platform.models import (
    Permission,
    PolicyState,
    Role,
    RoleAssignment,
    RolePermission,
    Site,
    Study,
    User,
)
from aurinara_platform.schemas import AuthorizationDecisionRequest
from aurinara_platform.services import AuthorizationService


def _request(user: User, action: str, study_id=None, site_id=None, product="edc"):
    return AuthorizationDecisionRequest.model_validate(
        {
            "principal": {"issuer": user.issuer, "subject": user.subject},
            "action": action,
            "resource": {
                "product": product,
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
    session.add(Site(id=site_id, study_id=study.id, name="Site One"))
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


def test_product_administrator_covers_every_study_only_within_its_product(
    session: Session,
) -> None:
    ensure_platform_access_defaults(session)
    user = User(issuer="https://issuer", subject="aurinara-admin")
    edc_user = User(issuer="https://issuer", subject="edc-admin")
    session.add_all([user, edc_user])
    session.flush()
    studies = [
        Study(code=f"PRODUCT-{index}", display_name=f"Study {index}", created_by=user.id)
        for index in (1, 2)
    ]
    session.add_all(studies)
    session.add_all(
        [
            Permission(
                code="aurinara.sdtm.write",
                product="aurinara",
                allowed_scope_levels=["study"],
            ),
            Permission(
                code="aurinara.permission.sync",
                product="aurinara",
                allowed_scope_levels=["system"],
            ),
            Permission(
                code="edc.form.enter",
                product="aurinara-edc",
                allowed_scope_levels=["study", "site"],
            ),
        ]
    )
    session.flush()
    ensure_product_role_defaults(session, "aurinara")
    ensure_product_role_defaults(session, "aurinara-edc")
    role = session.scalar(select(Role).where(Role.key == "aurinara.administrator"))
    edc_role = session.scalar(select(Role).where(Role.key == "edc.administrator"))
    assert role is not None
    assert edc_role is not None
    session.add_all(
        [
            RoleAssignment(user_id=user.id, role_id=role.id, assigned_by=user.id),
            RoleAssignment(user_id=edc_user.id, role_id=edc_role.id, assigned_by=user.id),
            PolicyState(id=1, version=1),
        ]
    )
    session.commit()

    authorization = AuthorizationService(Settings(environment="test"))
    for study in studies:
        assert authorization.decide(
            session, _request(user, "aurinara.sdtm.write", study.id, product="aurinara")
        ).allowed
        assert not authorization.decide(
            session, _request(user, "edc.form.enter", study.id, product="aurinara-edc")
        ).allowed
        assert authorization.decide(
            session, _request(edc_user, "edc.form.enter", study.id, product="aurinara-edc")
        ).allowed
        assert not authorization.decide(
            session, _request(edc_user, "aurinara.sdtm.write", study.id, product="aurinara")
        ).allowed
    assert not authorization.decide(
        session, _request(user, "aurinara.permission.sync", product="aurinara")
    ).allowed


def test_default_roles_cover_new_product_permissions_by_scope(session: Session) -> None:
    ensure_platform_access_defaults(session)
    system_admin = User(issuer="https://issuer", subject="system-admin")
    study_admin = User(issuer="https://issuer", subject="study-admin")
    viewer = User(issuer="https://issuer", subject="viewer")
    session.add_all([system_admin, study_admin, viewer])
    session.flush()
    study = Study(code="ROLE-1", display_name="Role Study", created_by=system_admin.id)
    permissions = [
        Permission(
            code="aurinara.sdtm.read",
            product="aurinara",
            module="sdtm",
            allowed_scope_levels=["study"],
        ),
        Permission(
            code="aurinara.sdtm.write",
            product="aurinara",
            module="sdtm",
            allowed_scope_levels=["study"],
        ),
    ]
    session.add_all([study, *permissions])
    session.flush()
    session.add_all(
        [
            RoleAssignment(
                user_id=system_admin.id,
                role_id=SYSTEM_ADMINISTRATOR_ROLE_ID,
                assigned_by=system_admin.id,
            ),
            RoleAssignment(
                user_id=study_admin.id,
                role_id=STUDY_ADMINISTRATOR_ROLE_ID,
                study_id=study.id,
                assigned_by=system_admin.id,
            ),
            RoleAssignment(
                user_id=viewer.id,
                role_id=STUDY_VIEWER_ROLE_ID,
                study_id=study.id,
                assigned_by=system_admin.id,
            ),
            PolicyState(id=1, version=1),
        ]
    )
    session.commit()
    service = AuthorizationService(Settings(environment="test"))

    assert service.decide(
        session,
        _request(system_admin, "aurinara.sdtm.write", study.id, product="aurinara"),
    ).allowed
    assert service.decide(
        session,
        _request(study_admin, "aurinara.sdtm.write", study.id, product="aurinara"),
    ).allowed
    assert service.decide(
        session,
        _request(viewer, "aurinara.sdtm.read", study.id, product="aurinara"),
    ).allowed
    assert not service.decide(
        session,
        _request(viewer, "aurinara.sdtm.write", study.id, product="aurinara"),
    ).allowed
    mismatch = service.decide(
        session,
        _request(study_admin, "aurinara.sdtm.read", study.id, product="aurinara-edc"),
    )
    assert not mismatch.allowed
    assert mismatch.reason_code == "permission_product_mismatch"
