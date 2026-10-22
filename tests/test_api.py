from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from aurinara_platform.auth import InvalidTokenError, Principal
from aurinara_platform.config import Settings
from aurinara_platform.default_access import (
    REGISTERED_STUDY_CREATOR_ROLE_ID,
    SYSTEM_ADMINISTRATOR_ROLE_ID,
    ensure_platform_access_defaults,
    ensure_registered_study_creator,
)
from aurinara_platform.main import create_app
from aurinara_platform.models import (
    Permission,
    PolicyState,
    Role,
    RoleAssignment,
    RoleChange,
    RolePermission,
    Study,
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


class ServiceVerifier:
    def verify(self, token: str) -> Principal:
        if token != "service-token":
            raise InvalidTokenError("invalid")
        return Principal("https://issuer", "edc-service")


class AdminAndServiceVerifier:
    def verify(self, token: str) -> Principal:
        if token == "admin-token":
            return Principal("https://issuer", "admin")
        if token == "service-token":
            return Principal("https://issuer", "edc-service")
        raise InvalidTokenError("invalid")


class CreatorVerifier:
    def verify(self, token: str) -> Principal:
        subjects = {"creator-a-token": "creator-a", "creator-b-token": "creator-b"}
        if token not in subjects:
            raise InvalidTokenError("invalid")
        return Principal("https://issuer", subjects[token])


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
        ensure_platform_access_defaults(session)
        admin = User(
            issuer="https://issuer",
            subject="admin",
            email="admin@example.test",
            display_name="Platform Admin",
        )
        role = session.get(Role, SYSTEM_ADMINISTRATOR_ROLE_ID)
        assert role is not None
        session.add_all([admin, PolicyState(id=1, version=1)])
        session.flush()
        session.add(
            RoleAssignment(
                user_id=admin.id,
                role_id=role.id,
                assigned_by=admin.id,
            )
        )
    return admin, role


def test_platform_manages_product_role_composition(session_factory) -> None:
    _seed_admin(session_factory)
    with session_factory.begin() as session:
        service_user = User(issuer="https://issuer", subject="edc-service")
        service_role = Role(
            product="aurinara-edc",
            name="aurinara-edc Service",
            assignable=False,
        )
        sync_permission = Permission(
            code="edc.permission.sync",
            product="aurinara-edc",
            module="platform",
            allowed_scope_levels=["system"],
        )
        session.add_all([service_user, service_role, sync_permission])
        session.flush()
        session.add_all(
            [
                RolePermission(role_id=service_role.id, permission_code=sync_permission.code),
                RoleAssignment(
                    user_id=service_user.id,
                    role_id=service_role.id,
                    assigned_by=service_user.id,
                ),
            ]
        )
    app = create_app(
        Settings(environment="test"),
        session_factory=session_factory,
        token_verifier=AdminAndServiceVerifier(),
        object_storage=NullStorage(),
    )
    client = TestClient(app)
    catalog = client.put(
        "/v1/products/aurinara-edc/permissions",
        headers={"Authorization": "Bearer service-token"},
        json={
            "permissions": [
                {
                    "code": "edc.designer.odm.export",
                    "description": "Export ODM metadata",
                    "module": "designer",
                    "allowed_scope_levels": ["system", "study"],
                },
                {
                    "code": "edc.designer.odm.import",
                    "description": "Import ODM metadata",
                    "module": "designer",
                    "allowed_scope_levels": ["system", "study"],
                },
            ]
        },
    )
    assert catalog.status_code == 200
    assert {item["code"] for item in catalog.json()} == {
        "edc.designer.odm.export",
        "edc.designer.odm.import",
    }
    roles_after_sync = client.get(
        "/v1/roles?product=aurinara-edc",
        headers={"Authorization": "Bearer admin-token"},
    )
    designer = next(item for item in roles_after_sync.json() if item["key"] == "edc.designer")
    assert designer["built_in"] is True
    assert designer["permission_codes"] == [
        "edc.designer.odm.export",
        "edc.designer.odm.import",
    ]

    created = client.post(
        "/v1/products/aurinara-edc/roles",
        headers={"Authorization": "Bearer admin-token"},
        json={
            "key": "custom.odm_designer",
            "name": "ODM Designer",
            "description": "Manage ODM metadata",
            "scope_level": "study",
            "permission_codes": [
                "edc.designer.odm.export",
                "edc.designer.odm.import",
            ],
            "reason": "Establish the ODM authoring role",
        },
    )
    assert created.status_code == 201
    role = created.json()
    assert role["key"] == "custom.odm_designer"
    assert role["built_in"] is False
    assert role["source_role_id"] is None
    assert role["permission_codes"] == [
        "edc.designer.odm.export",
        "edc.designer.odm.import",
    ]

    missing_reason = client.patch(
        f"/v1/roles/{role['id']}",
        headers={"Authorization": "Bearer admin-token"},
        json={"status": "inactive"},
    )
    assert missing_reason.status_code == 422

    missing_revision = client.patch(
        f"/v1/roles/{role['id']}",
        headers={"Authorization": "Bearer admin-token"},
        json={
            "status": "inactive",
            "reason": "Temporarily retire ODM authoring",
        },
    )
    assert missing_revision.status_code == 428

    updated = client.patch(
        f"/v1/roles/{role['id']}",
        headers={"Authorization": "Bearer admin-token", "If-Match": '"1"'},
        json={
            "status": "inactive",
            "reason": "Temporarily retire ODM authoring",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "inactive"
    assert updated.json()["assignable"] is False

    stale_update = client.patch(
        f"/v1/roles/{role['id']}",
        headers={"Authorization": "Bearer admin-token", "If-Match": '"1"'},
        json={
            "status": "active",
            "reason": "Attempt to save a stale editor state",
        },
    )
    assert stale_update.status_code == 409
    assert stale_update.json()["error"]["code"] == "role_revision_conflict"

    changes = client.get(
        f"/v1/roles/{role['id']}/changes",
        headers={"Authorization": "Bearer admin-token"},
    )
    assert changes.status_code == 200
    assert [item["action"] for item in changes.json()] == ["deactivate", "create"]
    with session_factory() as session:
        assert (
            len(
                session.scalars(
                    select(RoleChange).where(RoleChange.role_id == UUID(role["id"]))
                ).all()
            )
            == 2
        )


def test_user_study_and_membership_lifecycle(session_factory) -> None:
    admin, _ = _seed_admin(session_factory)
    with session_factory.begin() as session:
        study_role = Role(product="platform", name="Study Member", scope_level="study")
        edc_study_role = Role(product="aurinara-edc", name="EDC Study Member", scope_level="study")
        session.add_all([study_role, edc_study_role])
        session.flush()
        study_role_id = study_role.id
        edc_study_role_id = edc_study_role.id
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
        json={"code": "API-1", "display_name": "API Study", "sponsor": "API Sponsor"},
    )
    assert created_study.status_code == 201
    assert created_study.json()["sponsor"] == "API Sponsor"
    study_id = created_study.json()["id"]
    membership = client.put(
        f"/v1/studies/{study_id}/members/{new_user_id}",
        headers=headers,
        json={"product": "platform", "role_ids": [str(study_role_id)]},
    )
    assert membership.status_code == 200
    assert membership.json()[0]["user_id"] == str(new_user_id)
    edc_membership = client.put(
        f"/v1/studies/{study_id}/members/{new_user_id}",
        headers=headers,
        json={"product": "aurinara-edc", "role_ids": [str(edc_study_role_id)]},
    )
    assert edc_membership.status_code == 200
    memberships = client.get(f"/v1/studies/{study_id}/memberships", headers=headers).json()
    new_user_memberships = [item for item in memberships if item["user_id"] == str(new_user_id)]
    assert len(new_user_memberships) == 2

    removed = client.delete(
        f"/v1/studies/{study_id}/members/{new_user_id}?product=platform",
        headers=headers,
    )
    assert removed.status_code == 204
    remaining = client.get(f"/v1/studies/{study_id}/memberships", headers=headers).json()
    new_user_remaining = [item for item in remaining if item["user_id"] == str(new_user_id)]
    assert [item["product"] for item in new_user_remaining] == ["aurinara-edc"]


def test_registered_creators_only_see_their_own_studies(session_factory) -> None:
    _seed_admin(session_factory)
    with session_factory.begin() as session:
        creators = [
            User(
                issuer="https://issuer",
                subject=subject,
                email=f"{subject}@example.test",
                display_name=subject,
            )
            for subject in ("creator-a", "creator-b")
        ]
        session.add_all(creators)
        session.flush()
        for creator in creators:
            ensure_registered_study_creator(session, creator)

    app = create_app(
        Settings(environment="test"),
        session_factory=session_factory,
        token_verifier=CreatorVerifier(),
        object_storage=NullStorage(),
    )
    client = TestClient(app)
    headers_a = {"Authorization": "Bearer creator-a-token"}
    headers_b = {"Authorization": "Bearer creator-b-token"}
    study_a = client.post(
        "/v1/studies",
        headers=headers_a,
        json={"code": "OWN-A", "display_name": "Owner A", "sponsor": "Sponsor"},
    )
    study_b = client.post(
        "/v1/studies",
        headers=headers_b,
        json={"code": "OWN-B", "display_name": "Owner B", "sponsor": "Sponsor"},
    )
    assert study_a.status_code == 201
    assert study_b.status_code == 201
    assert [item["code"] for item in client.get("/v1/studies", headers=headers_a).json()] == [
        "OWN-A"
    ]
    assert [item["code"] for item in client.get("/v1/studies", headers=headers_b).json()] == [
        "OWN-B"
    ]
    denied = client.get(f"/v1/studies/{study_b.json()['id']}", headers=headers_a)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "authorization_denied"
    assert client.get("/v1/users", headers=headers_a).status_code == 403
    final_owner = client.delete(
        f"/v1/studies/{study_a.json()['id']}/members/{creators[0].id}?product=platform",
        headers=headers_a,
    )
    assert final_owner.status_code == 409
    assert final_owner.json()["error"]["code"] == "last_study_owner"


def test_study_creation_uses_platform_study_administrator_without_product_roles(
    session_factory,
) -> None:
    with session_factory.begin() as session:
        ensure_platform_access_defaults(session)
        creator = User(
            issuer="https://issuer",
            subject="creator-a",
            email="creator-a@example.test",
            display_name="Creator A",
        )
        session.add_all([creator, PolicyState(id=1, version=1)])
        session.flush()
        ensure_registered_study_creator(session, creator)
    app = create_app(
        Settings(environment="test"),
        session_factory=session_factory,
        token_verifier=CreatorVerifier(),
        object_storage=NullStorage(),
    )

    response = TestClient(app).post(
        "/v1/studies",
        headers={"Authorization": "Bearer creator-a-token"},
        json={"code": "NOT-READY", "display_name": "Not Ready", "sponsor": "Sponsor"},
    )

    assert response.status_code == 201
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Study)) == 1


def test_platform_admin_can_manage_system_product_access(session_factory) -> None:
    admin, _ = _seed_admin(session_factory)
    product_role_id = uuid4()
    with session_factory.begin() as session:
        session.add(
            Role(
                id=product_role_id,
                product="aurinara-edc",
                name="EDC System Administrator",
            )
        )
    app = create_app(
        Settings(environment="test"),
        session_factory=session_factory,
        token_verifier=FakeVerifier(),
        object_storage=NullStorage(),
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer admin-token"}

    assert client.get("/v1/users", headers=headers).status_code == 200
    roles = client.get("/v1/roles?product=aurinara-edc", headers=headers)
    assert roles.status_code == 200
    system_admin = next(item for item in roles.json() if item["name"] == "EDC System Administrator")
    assert system_admin["id"] == str(product_role_id)
    all_roles = client.get("/v1/roles", headers=headers)
    assert {item["product"] for item in all_roles.json()} == {"platform", "aurinara-edc"}

    assigned = client.put(
        f"/v1/users/{admin.id}/system-assignments",
        headers=headers,
        json={"product": "aurinara-edc", "role_ids": [str(product_role_id)]},
    )
    assert assigned.status_code == 200
    assert assigned.json()[0]["role_name"] == "EDC System Administrator"
    listed = client.get(f"/v1/users/{admin.id}/system-assignments", headers=headers)
    assert listed.json()[0]["role_id"] == str(product_role_id)

    revoked = client.put(
        f"/v1/users/{admin.id}/system-assignments",
        headers=headers,
        json={"product": "aurinara-edc", "role_ids": []},
    )
    assert revoked.status_code == 200
    assert revoked.json() == []


def test_system_access_edit_preserves_automatic_study_creator(session_factory) -> None:
    _seed_admin(session_factory)
    with session_factory.begin() as session:
        creator = User(
            issuer="https://issuer",
            subject="study-creator",
            email="creator@example.test",
        )
        session.add(creator)
        session.flush()
        ensure_registered_study_creator(session, creator)

    app = create_app(
        Settings(environment="test"),
        session_factory=session_factory,
        token_verifier=FakeVerifier(),
        object_storage=NullStorage(),
    )
    client = TestClient(app)
    response = client.put(
        f"/v1/users/{creator.id}/system-assignments",
        headers={"Authorization": "Bearer admin-token"},
        json={"product": "platform", "role_ids": []},
    )
    assert response.status_code == 200
    with session_factory() as session:
        assert (
            session.scalar(
                select(RoleAssignment.id).where(
                    RoleAssignment.user_id == creator.id,
                    RoleAssignment.role_id == REGISTERED_STUDY_CREATOR_ROLE_ID,
                )
            )
            is not None
        )


def test_product_role_catalog_sync_is_not_exposed(session_factory) -> None:
    admin, _ = _seed_admin(session_factory)
    source_role_id = uuid4()
    with session_factory.begin() as session:
        service_user = User(
            issuer="https://issuer",
            subject="edc-service",
            display_name="EDC service",
        )
        service_role = Role(
            product="aurinara-edc",
            name="aurinara-edc Service",
            assignable=False,
        )
        sync_permission = Permission(code="edc.role.sync", product="aurinara-edc")
        user_list = Permission(
            code="edc.user.list",
            product="aurinara-edc",
            module="shared",
            allowed_scope_levels=["system"],
        )
        study_read = Permission(
            code="edc.study.read",
            product="aurinara-edc",
            module="operational",
            allowed_scope_levels=["system", "study"],
        )
        legacy_role = Role(
            id=source_role_id,
            product="aurinara-edc",
            name="Legacy EDC Administrator",
            source_role_id=None,
        )
        session.add_all(
            [
                service_user,
                service_role,
                sync_permission,
                user_list,
                study_read,
                legacy_role,
            ]
        )
        session.flush()
        session.add_all(
            [
                RolePermission(role_id=service_role.id, permission_code=sync_permission.code),
                RolePermission(role_id=legacy_role.id, permission_code=user_list.code),
                RoleAssignment(
                    user_id=service_user.id,
                    role_id=service_role.id,
                    assigned_by=admin.id,
                ),
                RoleAssignment(
                    user_id=admin.id,
                    role_id=legacy_role.id,
                    assigned_by=admin.id,
                ),
            ]
        )
    app = create_app(
        Settings(environment="test"),
        session_factory=session_factory,
        token_verifier=ServiceVerifier(),
        object_storage=NullStorage(),
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer service-token"}
    response = client.put(
        "/v1/products/aurinara-edc/roles",
        headers=headers,
        json={
            "roles": [
                {
                    "source_role_id": str(source_role_id),
                    "name": "EDC System Administrator",
                    "description": "Full EDC access",
                    "scope_level": "system",
                    "permissions": ["edc.user.list", "edc.study.read"],
                },
            ]
        },
    )
    assert response.status_code == 405


def test_ctms_site_sync_populates_study_membership_options(session_factory) -> None:
    admin, _ = _seed_admin(session_factory)
    with session_factory.begin() as session:
        site_role = Role(
            product="aurinara-edc",
            name="Site Coordinator",
            scope_level="site",
        )
        service_user = User(
            issuer="https://issuer",
            subject="edc-service",
            display_name="EDC service",
        )
        service_role = Role(
            product="aurinara-edc",
            name="aurinara-edc Service",
            assignable=False,
        )
        site_sync = Permission(code="edc.site.sync", product="aurinara-edc")
        session.add_all([site_role, service_user, service_role, site_sync])
        session.flush()
        session.add_all(
            [
                RolePermission(role_id=service_role.id, permission_code=site_sync.code),
                RoleAssignment(
                    user_id=service_user.id,
                    role_id=service_role.id,
                    assigned_by=admin.id,
                ),
            ]
        )
        site_role_id = site_role.id

    admin_app = create_app(
        Settings(environment="test"),
        session_factory=session_factory,
        token_verifier=FakeVerifier(),
        object_storage=NullStorage(),
    )
    admin_client = TestClient(admin_app)
    admin_headers = {"Authorization": "Bearer admin-token"}
    created_study = admin_client.post(
        "/v1/studies",
        headers=admin_headers,
        json={"code": "SITE-1", "display_name": "Site Study", "sponsor": "Sponsor"},
    )
    study_id = created_study.json()["id"]
    site_id = uuid4()

    service_app = create_app(
        Settings(environment="test"),
        session_factory=session_factory,
        token_verifier=ServiceVerifier(),
        object_storage=NullStorage(),
    )
    synchronized = TestClient(service_app).put(
        f"/v1/products/aurinara-edc/studies/{study_id}/sites/{site_id}",
        headers={"Authorization": "Bearer service-token"},
        json={
            "name": "General Hospital",
            "country": "US",
            "state": "New York",
            "city": "New York",
        },
    )
    assert synchronized.status_code == 200
    assert synchronized.json()["id"] == str(site_id)

    sites = admin_client.get(f"/v1/studies/{study_id}/sites", headers=admin_headers)
    assert sites.status_code == 200
    assert [(item["id"], item["name"]) for item in sites.json()] == [
        (str(site_id), "General Hospital")
    ]

    assigned = admin_client.put(
        f"/v1/studies/{study_id}/members/{admin.id}",
        headers=admin_headers,
        json={
            "product": "aurinara-edc",
            "role_ids": [str(site_role_id)],
            "site_id": str(site_id),
        },
    )
    assert assigned.status_code == 200
    assert assigned.json()[0]["site_id"] == str(site_id)

    rejected = admin_client.put(
        f"/v1/studies/{study_id}/members/{admin.id}",
        headers=admin_headers,
        json={
            "product": "aurinara-edc",
            "role_ids": [str(site_role_id)],
            "site_id": str(uuid4()),
        },
    )
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "authorization_denied"

    other_study = admin_client.post(
        "/v1/studies",
        headers=admin_headers,
        json={"code": "SITE-2", "display_name": "Other Study", "sponsor": "Sponsor"},
    ).json()
    other_site_id = uuid4()
    other_site = TestClient(service_app).put(
        f"/v1/products/aurinara-edc/studies/{other_study['id']}/sites/{other_site_id}",
        headers={"Authorization": "Bearer service-token"},
        json={"name": "Other Hospital", "state": "Ohio", "city": "Columbus"},
    )
    assert other_site.status_code == 200
    cross_study = admin_client.put(
        f"/v1/studies/{study_id}/members/{admin.id}",
        headers=admin_headers,
        json={
            "product": "aurinara-edc",
            "role_ids": [str(site_role_id)],
            "site_id": str(other_site_id),
        },
    )
    assert cross_study.status_code == 403
    assert cross_study.json()["error"]["code"] == "authorization_denied"


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
