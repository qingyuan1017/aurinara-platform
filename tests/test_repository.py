from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from aurinara_platform.auth import InvalidTokenError, Principal
from aurinara_platform.config import Settings
from aurinara_platform.main import create_app
from aurinara_platform.models import (
    Permission,
    PolicyState,
    RepositoryFileVersion,
    RepositoryNode,
    RepositoryNodeRevision,
    Role,
    RoleAssignment,
    RolePermission,
    StoredObject,
    Study,
    User,
)
from aurinara_platform.storage import ObjectFacts


class RepositoryVerifier:
    def verify(self, token: str) -> Principal:
        subjects = {"admin-token": "admin", "viewer-token": "viewer"}
        if token not in subjects:
            raise InvalidTokenError("invalid")
        return Principal("https://issuer", subjects[token])


class RepositoryStorage:
    def create_multipart(self, key: str, media_type: str, sha256: str, retention_until=None) -> str:
        return "upload"

    def presign_upload_part(self, key: str, upload_id: str, part_number: int) -> str:
        return "https://upload.test"

    def complete_multipart(self, key: str, upload_id: str, parts) -> None: ...

    def abort_multipart(self, key: str, upload_id: str) -> None: ...

    def facts(self, key: str) -> ObjectFacts:
        return ObjectFacts(12, "a" * 64, "application/pdf")

    def presign_download(self, key: str) -> str:
        return f"https://download.test/{key}"

    def delete(self, key: str) -> None: ...


def _seed_repository(factory: sessionmaker[Session]) -> tuple[Study, StoredObject]:
    with factory.begin() as session:
        admin = User(issuer="https://issuer", subject="admin")
        viewer = User(issuer="https://issuer", subject="viewer")
        session.add_all([admin, viewer])
        session.flush()
        study = Study(
            code="REPO-001",
            display_name="Repository Study",
            sponsor="Synthetic Sponsor",
            created_by=admin.id,
        )
        session.add(study)
        session.flush()
        object_record = StoredObject(
            study_id=study.id,
            owner_product="aurinara",
            classification="confidential",
            sha256="a" * 64,
            size_bytes=12,
            media_type="application/pdf",
            object_key="test/study/a.pdf",
            status="available",
            created_by=admin.id,
        )
        admin_role = Role(product="platform", name="Repository Administrator")
        viewer_role = Role(product="platform", name="Repository Viewer")
        permissions = [
            Permission(
                code="platform.repository.read",
                product="platform",
                module="repository",
                allowed_scope_levels=["system", "study"],
            ),
            Permission(
                code="platform.repository.write",
                product="platform",
                module="repository",
                allowed_scope_levels=["system", "study"],
            ),
            Permission(
                code="aurinara.object.reference",
                product="aurinara",
                module="artifacts",
                allowed_scope_levels=["system", "study"],
            ),
            Permission(
                code="aurinara.artifact.read",
                product="aurinara",
                module="artifacts",
                allowed_scope_levels=["system", "study"],
            ),
            Permission(
                code="aurinara.artifact.write",
                product="aurinara",
                module="artifacts",
                allowed_scope_levels=["system", "study"],
            ),
        ]
        session.add_all(
            [object_record, admin_role, viewer_role, PolicyState(id=1)] + permissions
        )
        session.flush()
        for permission in permissions:
            session.add(RolePermission(role_id=admin_role.id, permission_code=permission.code))
        session.add(
            RolePermission(
                role_id=viewer_role.id, permission_code="platform.repository.read"
            )
        )
        session.add_all(
            [
                RoleAssignment(
                    user_id=admin.id, role_id=admin_role.id, assigned_by=admin.id
                ),
                RoleAssignment(
                    user_id=viewer.id, role_id=viewer_role.id, assigned_by=admin.id
                ),
            ]
        )
    return study, object_record


def test_publication_is_atomic_filtered_and_downloads_by_envelope(session_factory) -> None:
    study, object_record = _seed_repository(session_factory)
    app = create_app(
        Settings(environment="test"),
        session_factory=session_factory,
        token_verifier=RepositoryVerifier(),
        object_storage=RepositoryStorage(),
    )
    client = TestClient(app)
    admin_headers = {"Authorization": "Bearer admin-token"}

    published = client.post(
        f"/v1/objects/{object_record.id}/references",
        headers=admin_headers,
        json={
            "product": "aurinara",
            "external_reference": "artifact:report-1",
            "idempotency_key": "publish-report-1",
            "repository": {
                "filename": "validation-report.pdf",
                "source_type": "validation_report",
                "source_id": "report-1",
                "source_status": "current",
                "module": "validation",
                "classification": "confidential",
                "access": {
                    "read_action": "aurinara.artifact.read",
                    "write_action": "aurinara.artifact.write",
                },
                "provenance": {"validation_run_id": "run-1"},
            },
        },
    )
    assert published.status_code == 201

    repeated = client.post(
        f"/v1/objects/{object_record.id}/references",
        headers=admin_headers,
        json={
            "product": "aurinara",
            "external_reference": "artifact:report-1",
            "idempotency_key": "publish-report-1",
            "repository": {
                "filename": "validation-report.pdf",
                "source_type": "validation_report",
                "source_id": "report-1",
                "source_status": "current",
                "module": "validation",
                "classification": "confidential",
                "access": {
                    "read_action": "aurinara.artifact.read",
                    "write_action": "aurinara.artifact.write",
                },
                "provenance": {"validation_run_id": "run-1"},
            },
        },
    )
    assert repeated.status_code == 201

    browse = client.get(f"/v1/studies/{study.id}/repository?view=all", headers=admin_headers)
    assert browse.status_code == 200
    assert [item["display_name"] for item in browse.json()["items"]] == [
        "validation-report.pdf"
    ]
    node = browse.json()["items"][0]

    hidden = client.get(
        f"/v1/studies/{study.id}/repository?view=all",
        headers={"Authorization": "Bearer viewer-token"},
    )
    assert hidden.status_code == 200
    assert hidden.json() == {"items": [], "next_cursor": None}

    versions = client.get(
        f"/v1/repository/nodes/{node['id']}/versions", headers=admin_headers
    )
    assert versions.status_code == 200
    assert len(versions.json()) == 1
    download = client.post(
        f"/v1/repository/versions/{versions.json()[0]['id']}/download",
        headers=admin_headers,
    )
    assert download.status_code == 200
    assert download.json()["url"].endswith("test/study/a.pdf")

    renamed = client.patch(
        f"/v1/repository/nodes/{node['id']}",
        headers={**admin_headers, "If-Match": '"1"'},
        json={"name": "reviewed-report.pdf"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["revision"] == 2
    with session_factory() as session:
        assert len(session.scalars(select(RepositoryFileVersion)).all()) == 1
        assert [item.action for item in session.scalars(select(RepositoryNodeRevision))] == [
            "created",
            "renamed",
        ]

    rejected = client.patch(
        f"/v1/repository/nodes/{node['id']}/source-status",
        headers=admin_headers,
        json={"external_reference": "artifact:report-1", "source_status": "rejected"},
    )
    assert rejected.status_code == 200
    browse = client.get(f"/v1/studies/{study.id}/repository?view=all", headers=admin_headers)
    assert browse.json() == {"items": [], "next_cursor": None}
    assert (
        client.get(f"/v1/repository/nodes/{node['id']}", headers=admin_headers).status_code
        == 404
    )


def test_publication_builds_study_module_solution_purpose_hierarchy(session_factory) -> None:
    study, object_record = _seed_repository(session_factory)
    app = create_app(
        Settings(environment="test"),
        session_factory=session_factory,
        token_verifier=RepositoryVerifier(),
        object_storage=RepositoryStorage(),
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer admin-token"}
    payload = {
        "product": "aurinara",
        "external_reference": "artifact:source-1",
        "idempotency_key": "publish-source-1",
        "repository": {
            "filename": "dm.csv",
            "source_type": "source_file",
            "source_id": "source-1",
            "source_status": "current",
            "module": "raw",
            "solution": "raw-workbench:11111111-1111-1111-1111-111111111111",
            "solution_label": "RAW-01 · Primary Raw",
            "purpose": "source-files",
            "classification": "confidential",
            "access": {
                "read_action": "aurinara.artifact.read",
                "write_action": "aurinara.artifact.write",
            },
        },
    }

    for _ in range(2):
        response = client.post(
            f"/v1/objects/{object_record.id}/references",
            headers=headers,
            json=payload,
        )
        assert response.status_code == 201

    with session_factory.begin() as session:
        admin = session.scalar(select(User).where(User.subject == "admin"))
        assert admin is not None
        second_object = StoredObject(
            study_id=study.id,
            owner_product="aurinara",
            classification="confidential",
            sha256="b" * 64,
            size_bytes=12,
            media_type="application/pdf",
            object_key="test/study/b.pdf",
            status="available",
            created_by=admin.id,
        )
        session.add(second_object)
        session.flush()
        second_object_id = second_object.id
    second_payload = {
        **payload,
        "external_reference": "artifact:source-2",
        "idempotency_key": "publish-source-2",
        "repository": {
            **payload["repository"],
            "filename": "ae.csv",
            "source_id": "source-2",
            "solution": "raw-workbench:22222222-2222-2222-2222-222222222222",
            "solution_label": "RAW-02 · Secondary Raw",
        },
    }
    second_response = client.post(
        f"/v1/objects/{second_object_id}/references",
        headers=headers,
        json=second_payload,
    )
    assert second_response.status_code == 201

    root = client.get(
        f"/v1/studies/{study.id}/repository?view=folder", headers=headers
    )
    assert root.status_code == 200
    assert [item["display_name"] for item in root.json()["items"]] == [
        f"{study.code} · {study.display_name}"
    ]
    hidden_root = client.get(
        f"/v1/studies/{study.id}/repository?view=folder",
        headers={"Authorization": "Bearer viewer-token"},
    )
    assert hidden_root.json() == {"items": [], "next_cursor": None}
    managed = client.patch(
        f"/v1/repository/nodes/{root.json()['items'][0]['id']}",
        headers={**headers, "If-Match": '"1"'},
        json={"name": "Renamed study"},
    )
    assert managed.status_code == 409
    assert managed.json()["error"]["code"] == "repository_structure_managed"

    with session_factory() as session:
        nodes = session.scalars(select(RepositoryNode)).all()
        assert len(nodes) == 8
        assert {
            node.display_name
            for node in nodes
            if node.source_resource_type == "repository_structure_solution"
        } == {"RAW-01 · Primary Raw", "RAW-02 · Secondary Raw"}
        by_id = {node.id: node for node in nodes}
        file_node = next(node for node in nodes if node.display_name == "dm.csv")
        names = [file_node.display_name]
        parent_id = file_node.parent_id
        while parent_id is not None:
            parent = by_id[parent_id]
            names.append(parent.display_name)
            parent_id = parent.parent_id

        assert list(reversed(names)) == [
            f"{study.code} · {study.display_name}",
            "Raw",
            "RAW-01 · Primary Raw",
            "Source Files",
            "dm.csv",
        ]
