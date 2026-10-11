from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from aurinara_platform.auth import Principal
from aurinara_platform.config import Settings
from aurinara_platform.models import (
    ObjectReference,
    Permission,
    PolicyState,
    Role,
    RoleAssignment,
    RolePermission,
    StoredObject,
    Study,
    User,
)
from aurinara_platform.problems import Problem
from aurinara_platform.schemas import ObjectReferenceCreate, UploadFinalize, UploadSessionCreate
from aurinara_platform.services import AuthorizationService, ObjectService
from aurinara_platform.storage import ObjectFacts


@dataclass
class FakeStorage:
    sha256: str
    size_bytes: int
    deleted: bool = False
    aborted: bool = False

    def create_multipart(self, key: str, media_type: str, sha256: str, retention_until=None) -> str:
        assert sha256 == self.sha256
        return "upload-1"

    def presign_upload_part(self, key: str, upload_id: str, part_number: int) -> str:
        return f"https://upload.test/{part_number}"

    def complete_multipart(self, key: str, upload_id: str, parts: list[tuple[int, str]]) -> None:
        assert parts == [(1, "etag")]

    def abort_multipart(self, key: str, upload_id: str) -> None:
        self.aborted = True

    def facts(self, key: str) -> ObjectFacts:
        return ObjectFacts(
            size_bytes=self.size_bytes,
            sha256=self.sha256,
            media_type="application/pdf",
        )

    def presign_download(self, key: str) -> str:
        return "https://download.test/object"

    def delete(self, key: str) -> None:
        self.deleted = True


class CleanScanner:
    def scan(self, object_id: str) -> bool:
        UUID(object_id)
        return True


def _seed(session: Session):
    user = User(issuer="https://issuer", subject="user")
    session.add(user)
    session.flush()
    study = Study(code="OBJECTS", display_name="Objects", created_by=user.id)
    role = Role(product="artifact", name="Artifact objects")
    permissions = [
        Permission(code=code, product="artifact")
        for code in (
            "artifact.object.upload",
            "artifact.object.download",
            "artifact.object.reference",
        )
    ]
    session.add_all([study, role, *permissions])
    session.flush()
    session.add_all(
        [RolePermission(role_id=role.id, permission_code=item.code) for item in permissions]
    )
    session.add(
        RoleAssignment(
            user_id=user.id,
            role_id=role.id,
            study_id=study.id,
            assigned_by=user.id,
        )
    )
    session.add(PolicyState(id=1, version=1))
    session.commit()
    return user, study


def test_verified_object_becomes_available_without_exposing_key(session: Session) -> None:
    user, study = _seed(session)
    digest = "a" * 64
    storage = FakeStorage(digest, 12)
    settings = Settings(environment="test")
    service = ObjectService(settings, AuthorizationService(settings), storage, CleanScanner())
    principal = Principal(user.issuer, user.subject)

    upload = service.create_upload(
        session,
        UploadSessionCreate(
            study_id=study.id,
            owner_product="artifact",
            classification="clinical-artifact",
            media_type="application/pdf",
            expected_sha256=digest,
            expected_size_bytes=12,
            part_count=1,
        ),
        principal,
        user,
    )
    record = service.finalize(
        session,
        upload.object_id,
        UploadFinalize.model_validate({"parts": [{"part_number": 1, "etag": "etag"}]}),
        principal,
    )

    assert record.status == "available"
    assert service.download(session, record.id, principal).startswith("https://download.test")
    assert "/studies/" in record.object_key

    command = ObjectReferenceCreate(product="artifact", external_reference="artifact:retry")
    first = service.add_reference(session, record.id, command, principal, user)
    second = service.add_reference(session, record.id, command, principal, user)
    assert second.id == first.id


def test_checksum_mismatch_deletes_bytes_and_rejects_record(session: Session) -> None:
    user, study = _seed(session)
    digest = "b" * 64
    storage = FakeStorage("c" * 64, 12)
    settings = Settings(environment="test")
    service = ObjectService(settings, AuthorizationService(settings), storage, CleanScanner())
    principal = Principal(user.issuer, user.subject)
    # The fake must accept the requested upload digest before returning different facts.
    storage.sha256 = digest
    upload = service.create_upload(
        session,
        UploadSessionCreate(
            study_id=study.id,
            owner_product="artifact",
            classification="clinical-artifact",
            media_type="application/pdf",
            expected_sha256=digest,
            expected_size_bytes=12,
            part_count=1,
        ),
        principal,
        user,
    )
    storage.sha256 = "c" * 64

    with pytest.raises(Problem) as caught:
        service.finalize(
            session,
            upload.object_id,
            UploadFinalize.model_validate({"parts": [{"part_number": 1, "etag": "etag"}]}),
            principal,
        )

    assert caught.value.code == "object_verification_failed"
    assert storage.deleted is True


def test_incomplete_upload_is_rejected_before_storage_completion(session: Session) -> None:
    user, study = _seed(session)
    digest = "d" * 64
    storage = FakeStorage(digest, 12)
    settings = Settings(environment="test")
    service = ObjectService(settings, AuthorizationService(settings), storage, CleanScanner())
    principal = Principal(user.issuer, user.subject)
    upload = service.create_upload(
        session,
        UploadSessionCreate(
            study_id=study.id,
            owner_product="artifact",
            classification="clinical-artifact",
            media_type="application/pdf",
            expected_sha256=digest,
            expected_size_bytes=12,
            part_count=2,
        ),
        principal,
        user,
    )

    with pytest.raises(Problem) as caught:
        service.finalize(
            session,
            upload.object_id,
            UploadFinalize.model_validate({"parts": [{"part_number": 1, "etag": "etag"}]}),
            principal,
        )

    assert caught.value.code == "incomplete_upload"


def test_expired_upload_is_aborted_and_cannot_be_finalized(session: Session) -> None:
    user, study = _seed(session)
    digest = "e" * 64
    storage = FakeStorage(digest, 12)
    settings = Settings(environment="test")
    service = ObjectService(settings, AuthorizationService(settings), storage, CleanScanner())
    principal = Principal(user.issuer, user.subject)
    upload = service.create_upload(
        session,
        UploadSessionCreate(
            study_id=study.id,
            owner_product="artifact",
            classification="clinical-artifact",
            media_type="application/pdf",
            expected_sha256=digest,
            expected_size_bytes=12,
            part_count=1,
        ),
        principal,
        user,
    )
    record = session.get(StoredObject, upload.object_id)
    assert record is not None
    record.upload_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    with pytest.raises(Problem) as caught:
        service.finalize(
            session,
            upload.object_id,
            UploadFinalize.model_validate({"parts": [{"part_number": 1, "etag": "etag"}]}),
            principal,
        )

    assert caught.value.code == "upload_expired"
    assert record.status == "rejected"
    assert storage.aborted is True


def test_retention_and_cross_product_references_block_deletion(session: Session) -> None:
    user, study = _seed(session)
    storage = FakeStorage("f" * 64, 12)
    service = ObjectService(
        Settings(environment="test"),
        AuthorizationService(Settings(environment="test")),
        storage,
        CleanScanner(),
    )
    record = StoredObject(
        study_id=study.id,
        owner_product="artifact",
        classification="clinical-artifact",
        sha256="f" * 64,
        size_bytes=12,
        media_type="application/pdf",
        object_key=f"test/studies/{study.id}/sha256/ff/ff/{'f' * 64}",
        status="available",
        retention_until=datetime.now(UTC) + timedelta(days=1),
        created_by=user.id,
        finalized_at=datetime.now(UTC),
    )
    session.add(record)
    session.flush()
    session.add_all(
        [
            ObjectReference(
                object_id=record.id,
                product="aurinara",
                external_reference="artifact:1",
                created_by=user.id,
            ),
            ObjectReference(
                object_id=record.id,
                product="aurinara-edc",
                external_reference="attachment:1",
                created_by=user.id,
            ),
        ]
    )
    session.commit()

    assert service.purge_if_unreferenced(session, record.id) is False
    record.retention_until = datetime.now(UTC) - timedelta(seconds=1)
    for reference in session.query(ObjectReference).all():
        reference.released_at = datetime.now(UTC)
    session.commit()

    assert service.purge_if_unreferenced(session, record.id) is True
    assert storage.deleted is True
