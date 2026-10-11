from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from aurinara_platform.auth import InvalidTokenError, Principal
from aurinara_platform.config import Settings
from aurinara_platform.main import create_app
from aurinara_platform.models import (
    Permission,
    PolicyState,
    Role,
    RoleAssignment,
    RolePermission,
    User,
)
from aurinara_platform.storage import ObjectFacts


class FakeVerifier:
    def verify(self, token: str) -> Principal:
        if token != "admin-token":
            raise InvalidTokenError("invalid")
        return Principal("https://issuer", "admin")


class FirstUserVerifier:
    def verify(self, token: str) -> Principal:
        if token != "first-user-token":
            raise InvalidTokenError("invalid")
        return Principal(
            "https://issuer",
            "first-user",
            email="first@example.test",
            display_name="First Administrator",
        )


class NullStorage:
    def create_multipart(self, key: str, media_type: str, sha256: str, retention_until=None) -> str:
        return "upload"

    def presign_upload_part(self, key: str, upload_id: str, part_number: int) -> str:
        return "https://upload.test"

    def complete_multipart(self, key: str, upload_id: str, parts) -> None: ...

    def abort_multipart(self, key: str, upload_id: str) -> None: ...

    def facts(self, key: str) -> ObjectFacts:
        return ObjectFacts(1, "0" * 64, "application/octet-stream")

    def presign_download(self, key: str) -> str:
        return "https://download.test"

    def delete(self, key: str) -> None: ...


def _seed_admin(factory: sessionmaker[Session]) -> tuple[User, Role]:
    with factory.begin() as session:
        admin = User(
            issuer="https://issuer",
            subject="admin",
            email="admin@example.test",
            display_name="Platform Admin",
        )
        role = Role(product="platform", name="Platform Admin")
        codes = (
            "platform.user.manage",
            "platform.study.create",
            "platform.study.read",
            "platform.study.manage",
            "platform.membership.read",
            "platform.membership.manage",
        )
        session.add_all([admin, role, PolicyState(id=1, version=1)])
        session.flush()
        for code in codes:
            session.add(Permission(code=code, product="platform"))
            session.add(RolePermission(role_id=role.id, permission_code=code))
        session.add(
            RoleAssignment(
                user_id=admin.id,
                role_id=role.id,
                assigned_by=admin.id,
            )
        )
    return admin, role


def test_user_study_and_membership_lifecycle(session_factory) -> None:
    admin, _ = _seed_admin(session_factory)
    app = create_app(
        Settings(environment="test"),
        session_factory=session_factory,
        token_verifier=FakeVerifier(),
        object_storage=NullStorage(),
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer admin-token"}

    assert client.get("/v1/me", headers=headers).json()["id"] == str(admin.id)
    new_user_id = uuid4()
    created_user = client.post(
        "/v1/users",
        headers=headers,
        json={
            "id": str(new_user_id),
            "issuer": "https://issuer",
            "subject": "user-1",
            "email": "user@example.test",
            "display_name": "User One",
        },
    )
    assert created_user.status_code == 201
    assert created_user.headers["etag"] == '"1"'

    created_study = client.post(
        "/v1/studies",
        headers=headers,
        json={"code": "API-1", "display_name": "API Study"},
    )
    assert created_study.status_code == 201
    study_id = created_study.json()["id"]
    membership = client.put(
        f"/v1/studies/{study_id}/members/{new_user_id}",
        headers=headers,
        json={"role_ids": [str(_.id)]},
    )
    assert membership.status_code == 200
    assert membership.json()[0]["user_id"] == str(new_user_id)


def test_invalid_token_is_rejected(session_factory) -> None:
    _seed_admin(session_factory)
    app = create_app(
        Settings(environment="test"),
        session_factory=session_factory,
        token_verifier=FakeVerifier(),
        object_storage=NullStorage(),
    )
    response = TestClient(app).get("/v1/me", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


def test_development_can_bootstrap_exactly_one_first_administrator(session_factory) -> None:
    with session_factory() as session:
        session.add(PolicyState(id=1, version=1))
        session.commit()

    app = create_app(
        Settings(environment="development", bootstrap_first_user_as_admin=True),
        session_factory=session_factory,
        token_verifier=FirstUserVerifier(),
        object_storage=NullStorage(),
    )
    client = TestClient(app)

    response = client.get("/v1/me", headers={"Authorization": "Bearer first-user-token"})

    assert response.status_code == 200
    assert response.json()["email"] == "first@example.test"
    with session_factory() as session:
        user = session.scalar(select(User).where(User.subject == "first-user"))
        assert user is not None
        assignment = session.scalar(select(RoleAssignment).where(RoleAssignment.user_id == user.id))
        assert assignment is not None
        assert assignment.study_id is None
