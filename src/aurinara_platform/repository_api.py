"""HTTP contract for the central study Repository."""

import io
from datetime import UTC, datetime
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from aurinara_platform.api import get_current_user, get_principal, get_session
from aurinara_platform.auth import Principal
from aurinara_platform.models import (
    ObjectReference,
    ObjectStatus,
    RepositoryFileVersion,
    RepositoryNode,
    RepositoryUpload,
    StoredObject,
    User,
)
from aurinara_platform.problems import Problem
from aurinara_platform.repository import RepositoryService
from aurinara_platform.rich_repository import RichRepositoryService
from aurinara_platform.schemas import (
    DownloadResponse,
    ObjectReferenceCreate,
    OfficeSessionCreate,
    OfficeSessionResponse,
    PdfAnnotationCreate,
    PdfAnnotationPatch,
    PdfAnnotationResponse,
    RepositoryAccessEnvelope,
    RepositoryCollectionCapabilities,
    RepositoryConversionCreate,
    RepositoryExportCreate,
    RepositoryFolderCreate,
    RepositoryJobResponse,
    RepositoryNodePage,
    RepositoryNodePatch,
    RepositoryNodeResponse,
    RepositoryPublication,
    RepositoryStatusPatch,
    RepositoryUploadComplete,
    RepositoryUploadCreate,
    RepositoryUploadResponse,
    RepositoryVersionBindingResponse,
    RepositoryVersionCreate,
    RepositoryVersionResponse,
    TabularPreviewRequest,
    TabularPreviewResponse,
    UploadFinalize,
    UploadSessionCreate,
)
from aurinara_platform.services import ObjectService

router = APIRouter(tags=["repository"])


def get_repository(request: Request) -> RepositoryService:
    return cast(RepositoryService, request.app.state.repository_service)


def get_rich_repository(request: Request) -> RichRepositoryService:
    return cast(RichRepositoryService, request.app.state.rich_repository_service)


def get_objects(request: Request) -> ObjectService:
    return cast(ObjectService, request.app.state.object_service)


def _expected_revision(if_match: str | None) -> int:
    if if_match is None:
        raise Problem(428, "if_match_required", "If-Match is required")
    try:
        return int(if_match.strip('"'))
    except ValueError as error:
        raise Problem(400, "invalid_if_match", "If-Match must contain a revision") from error


def _node_response(
    node: RepositoryNode,
    session: Session | None = None,
    principal: Principal | None = None,
    rich: RichRepositoryService | None = None,
) -> RepositoryNodeResponse:
    response = RepositoryNodeResponse.model_validate(node)
    if session is None or principal is None or rich is None:
        return response
    if node.kind != "file":
        response.capabilities = rich.capabilities(session, principal, node, None)
        return response
    version = session.scalar(
        select(RepositoryFileVersion).where(
            RepositoryFileVersion.node_id == node.id,
            RepositoryFileVersion.version_number == node.current_version_number,
        )
    )
    if version is None:
        return response
    stored = session.get(StoredObject, version.object_id)
    if stored is None:
        return response
    response.current_version = _version_response(version, stored, session, principal, node, rich)
    response.capabilities = response.current_version.capabilities
    return response


def _version_response(
    version: RepositoryFileVersion,
    stored_object: StoredObject,
    session: Session | None = None,
    principal: Principal | None = None,
    node: RepositoryNode | None = None,
    rich: RichRepositoryService | None = None,
) -> RepositoryVersionResponse:
    response = RepositoryVersionResponse(
        id=version.id,
        node_id=version.node_id,
        version_number=version.version_number,
        object_id=version.object_id,
        object_reference_id=version.object_reference_id,
        sha256=stored_object.sha256 or "",
        size_bytes=stored_object.size_bytes or 0,
        media_type=stored_object.media_type,
        derived_from_version_id=version.derived_from_version_id,
        created_by=version.created_by,
        created_at=version.created_at,
    )
    if session is not None and rich is not None:
        response.profile = rich.profile(session, version.id)
        if principal is not None and node is not None:
            response.capabilities = rich.capabilities(session, principal, node, version)
    return response


@router.post(
    "/object-references/{reference_id}/repository",
    response_model=RepositoryNodeResponse,
    status_code=status.HTTP_201_CREATED,
)
def publish_existing_repository_reference(
    reference_id: UUID,
    command: RepositoryPublication,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    actor: Annotated[User, Depends(get_current_user)],
    service: Annotated[RepositoryService, Depends(get_repository)],
) -> RepositoryNodeResponse:
    """Publish reviewed Repository metadata for an exact existing reference."""

    return _node_response(
        service.publish_existing_reference(session, reference_id, command, principal, actor)
    )


@router.get(
    "/studies/{study_id}/repository",
    response_model=RepositoryNodePage,
)
def browse_repository(
    study_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[RepositoryService, Depends(get_repository)],
    rich: Annotated[RichRepositoryService, Depends(get_rich_repository)],
    parent_id: Annotated[UUID | None, Query()] = None,
    view: Annotated[
        Literal["folder", "all", "recent", "aurinara", "aurinara-edc", "platform", "archived"],
        Query(),
    ] = "folder",
    q: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    product: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    module: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    classification: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    source_status: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    sort: Annotated[Literal["updated_desc"], Query()] = "updated_desc",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after: Annotated[str | None, Query()] = None,
) -> RepositoryNodePage:
    del sort
    items, next_cursor = service.list_nodes(
        session,
        study_id,
        principal,
        parent_id=parent_id,
        view=view,
        query=q,
        product=product,
        module=module,
        classification=classification,
        source_status=source_status,
        limit=limit,
        after=after,
    )
    can_manage = False
    try:
        service._require_repository(session, principal, "platform.repository.write", study_id)
        if parent_id is not None:
            service.get_node(session, parent_id, principal, write=True)
        can_manage = True
    except Problem:
        pass
    return RepositoryNodePage(
        items=[_node_response(item, session, principal, rich) for item in items],
        next_cursor=next_cursor,
        capabilities=RepositoryCollectionCapabilities(
            can_upload=can_manage, can_create_folder=can_manage
        ),
    )


@router.post(
    "/studies/{study_id}/repository/folders",
    response_model=RepositoryNodeResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_repository_folder(
    study_id: UUID,
    command: RepositoryFolderCreate,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    actor: Annotated[User, Depends(get_current_user)],
    service: Annotated[RepositoryService, Depends(get_repository)],
) -> RepositoryNodeResponse:
    node = service.create_folder(session, study_id, command, principal, actor)
    response.headers["ETag"] = f'"{node.revision}"'
    response.headers["Location"] = f"/v1/repository/nodes/{node.id}"
    return _node_response(node)


@router.get("/repository/nodes/{node_id}", response_model=RepositoryNodeResponse)
def get_repository_node(
    node_id: UUID,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[RepositoryService, Depends(get_repository)],
    rich: Annotated[RichRepositoryService, Depends(get_rich_repository)],
) -> RepositoryNodeResponse:
    node = service.get_node(session, node_id, principal)
    response.headers["ETag"] = f'"{node.revision}"'
    return _node_response(node, session, principal, rich)


@router.patch("/repository/nodes/{node_id}", response_model=RepositoryNodeResponse)
def patch_repository_node(
    node_id: UUID,
    command: RepositoryNodePatch,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    actor: Annotated[User, Depends(get_current_user)],
    service: Annotated[RepositoryService, Depends(get_repository)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> RepositoryNodeResponse:
    node = service.patch_node(
        session, node_id, command, _expected_revision(if_match), principal, actor
    )
    response.headers["ETag"] = f'"{node.revision}"'
    return _node_response(node)


@router.delete("/repository/nodes/{node_id}", response_model=RepositoryNodeResponse)
def archive_repository_node(
    node_id: UUID,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    actor: Annotated[User, Depends(get_current_user)],
    service: Annotated[RepositoryService, Depends(get_repository)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> RepositoryNodeResponse:
    node = service.archive(session, node_id, _expected_revision(if_match), principal, actor)
    response.headers["ETag"] = f'"{node.revision}"'
    return _node_response(node)


@router.post("/repository/nodes/{node_id}/restore", response_model=RepositoryNodeResponse)
def restore_repository_node(
    node_id: UUID,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    actor: Annotated[User, Depends(get_current_user)],
    service: Annotated[RepositoryService, Depends(get_repository)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> RepositoryNodeResponse:
    node = service.restore(session, node_id, _expected_revision(if_match), principal, actor)
    response.headers["ETag"] = f'"{node.revision}"'
    return _node_response(node)


@router.post(
    "/repository/nodes/{node_id}/versions",
    response_model=RepositoryVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_repository_version(
    node_id: UUID,
    command: RepositoryVersionCreate,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    actor: Annotated[User, Depends(get_current_user)],
    service: Annotated[RepositoryService, Depends(get_repository)],
    rich: Annotated[RichRepositoryService, Depends(get_rich_repository)],
) -> RepositoryVersionResponse:
    version = service.add_version(session, node_id, command, principal, actor)
    stored_object = session.get(StoredObject, version.object_id)
    assert stored_object is not None
    node = session.get(RepositoryNode, version.node_id)
    assert node is not None
    return _version_response(version, stored_object, session, principal, node, rich)


@router.get(
    "/repository/nodes/{node_id}/versions",
    response_model=list[RepositoryVersionResponse],
)
def list_repository_versions(
    node_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[RepositoryService, Depends(get_repository)],
    rich: Annotated[RichRepositoryService, Depends(get_rich_repository)],
) -> list[RepositoryVersionResponse]:
    node = service.get_node(session, node_id, principal)
    return [
        _version_response(version, stored_object, session, principal, node, rich)
        for version, stored_object in service.list_versions(session, node_id, principal)
    ]


@router.get("/repository/versions/{version_id}", response_model=RepositoryVersionBindingResponse)
def get_repository_version(
    version_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[RepositoryService, Depends(get_repository)],
    rich: Annotated[RichRepositoryService, Depends(get_rich_repository)],
) -> RepositoryVersionBindingResponse:
    node, version, stored_object = service.resolve_version(session, version_id, principal)
    base = _version_response(version, stored_object, session, principal, node, rich)
    return RepositoryVersionBindingResponse(
        **base.model_dump(),
        study_id=node.study_id,
        filename=node.display_name,
        classification=node.classification,
        source_product=node.source_product,
        source_status=node.source_status,
        site_id=node.site_id,
    )


@router.post("/repository/versions/{version_id}/download", response_model=DownloadResponse)
def download_repository_version(
    version_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[RepositoryService, Depends(get_repository)],
) -> DownloadResponse:
    url, expires_at = service.download(session, version_id, principal)
    return DownloadResponse(url=url, expires_at=expires_at)


@router.patch("/repository/nodes/{node_id}/source-status", response_model=RepositoryNodeResponse)
def synchronize_repository_source_status(
    node_id: UUID,
    command: RepositoryStatusPatch,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    actor: Annotated[User, Depends(get_current_user)],
    service: Annotated[RepositoryService, Depends(get_repository)],
) -> RepositoryNodeResponse:
    return _node_response(service.synchronize_status(session, node_id, command, principal, actor))


@router.post(
    "/studies/{study_id}/repository/uploads",
    response_model=RepositoryUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
def start_repository_upload(
    study_id: UUID,
    command: RepositoryUploadCreate,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    actor: Annotated[User, Depends(get_current_user)],
    repository: Annotated[RepositoryService, Depends(get_repository)],
    objects: Annotated[ObjectService, Depends(get_objects)],
) -> RepositoryUploadResponse:
    existing_intent = session.scalar(
        select(RepositoryUpload).where(RepositoryUpload.idempotency_key == command.idempotency_key)
    )
    if existing_intent is not None:
        if existing_intent.study_id != study_id or existing_intent.filename != command.filename:
            raise Problem(409, "idempotency_conflict", "The idempotency key is already used")
        record = session.get(StoredObject, existing_intent.object_id)
        assert record is not None
        if record.status == ObjectStatus.pending:
            resumed = objects.resume_upload(session, record.id, principal)
            return RepositoryUploadResponse(
                object_id=record.id,
                upload_id=resumed.upload_id,
                expires_at=resumed.expires_at,
                parts=resumed.parts,
                filename=existing_intent.filename,
                replacement_node_id=existing_intent.replacement_node_id,
                already_available=False,
            )
        return RepositoryUploadResponse(
            object_id=record.id,
            upload_id=record.multipart_upload_id or "already-available",
            expires_at=record.upload_expires_at or datetime.now(UTC),
            parts=[],
            filename=existing_intent.filename,
            replacement_node_id=existing_intent.replacement_node_id,
            already_available=record.status == ObjectStatus.available,
        )
    destination = None
    if command.replacement_node_id is not None:
        destination = repository.get_node(
            session, command.replacement_node_id, principal, write=True
        )
        if destination.kind != "file" or destination.study_id != study_id:
            raise Problem(
                422, "repository_upload_destination_invalid", "The replacement is invalid"
            )
    elif command.parent_id is not None:
        destination = repository.get_node(session, command.parent_id, principal, write=True)
        if destination.kind != "folder" or destination.study_id != study_id:
            raise Problem(422, "repository_upload_destination_invalid", "The parent is invalid")
    else:
        repository._require_repository(session, principal, "platform.repository.write", study_id)
    classification = (
        destination.classification if destination is not None else command.classification
    )
    available = session.scalar(
        select(StoredObject).where(
            StoredObject.study_id == study_id,
            StoredObject.sha256 == command.sha256,
            StoredObject.status == ObjectStatus.available,
        )
    )
    if available is None:
        upload = objects.create_upload(
            session,
            UploadSessionCreate(
                study_id=study_id,
                owner_product="platform",
                authorization_action="platform.repository.write",
                classification=classification,
                media_type=command.media_type,
                expected_sha256=command.sha256,
                expected_size_bytes=command.size_bytes,
                part_count=command.part_count,
            ),
            principal,
            actor,
        )
        object_id = upload.object_id
        upload_id = upload.upload_id
        expires_at = upload.expires_at
        parts = upload.parts
    else:
        object_id = available.id
        upload_id = "already-available"
        expires_at = datetime.now(UTC)
        parts = []
    session.add(
        RepositoryUpload(
            object_id=object_id,
            study_id=study_id,
            parent_id=command.parent_id,
            replacement_node_id=command.replacement_node_id,
            filename=command.filename.strip(),
            classification=classification,
            idempotency_key=command.idempotency_key,
            created_by=actor.id,
        )
    )
    return RepositoryUploadResponse(
        object_id=object_id,
        upload_id=upload_id,
        expires_at=expires_at,
        parts=parts,
        filename=command.filename.strip(),
        replacement_node_id=command.replacement_node_id,
        already_available=available is not None,
    )


@router.post(
    "/repository/uploads/{object_id}/complete",
    response_model=RepositoryNodeResponse,
)
def complete_repository_upload(
    object_id: UUID,
    command: RepositoryUploadComplete,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    actor: Annotated[User, Depends(get_current_user)],
    repository: Annotated[RepositoryService, Depends(get_repository)],
    rich: Annotated[RichRepositoryService, Depends(get_rich_repository)],
    objects: Annotated[ObjectService, Depends(get_objects)],
) -> RepositoryNodeResponse:
    intent = session.scalar(
        select(RepositoryUpload)
        .where(
            RepositoryUpload.object_id == object_id,
            RepositoryUpload.created_by == actor.id,
        )
        .order_by(RepositoryUpload.created_at.desc())
    )
    if intent is None:
        raise Problem(404, "repository_upload_not_found", "The Repository upload was not found")
    record = session.get(StoredObject, object_id)
    if record is None:
        raise Problem(404, "object_not_found", "The object was not found")
    if intent.completed_at is not None:
        link = session.scalar(
            select(RepositoryFileVersion)
            .where(RepositoryFileVersion.object_id == object_id)
            .order_by(RepositoryFileVersion.created_at.desc())
        )
        if link is None:
            raise Problem(409, "repository_upload_incomplete", "The upload has no Repository file")
        node = repository.get_node(session, link.node_id, principal)
        return _node_response(node, session, principal, rich)
    if record.status == ObjectStatus.pending:
        record = objects.finalize(
            session,
            object_id,
            UploadFinalize(parts=command.parts),
            principal,
        )
    elif record.status != ObjectStatus.available:
        raise Problem(409, "invalid_object_state", "The upload cannot be completed")
    destination = None
    if intent.replacement_node_id is not None:
        destination = repository.get_node(
            session, intent.replacement_node_id, principal, write=True
        )
    elif intent.parent_id is not None:
        destination = repository.get_node(session, intent.parent_id, principal, write=True)
    else:
        repository._require_repository(
            session, principal, "platform.repository.write", intent.study_id
        )
    product = destination.source_product if destination is not None else "platform"
    external = f"repository-upload:{intent.id}"
    reference = ObjectReference(
        object_id=record.id,
        product=product,
        authorization_action=(
            destination.required_write_action
            if destination is not None
            else "platform.repository.write"
        ),
        external_reference=external,
        idempotency_key=external,
        created_by=actor.id,
    )
    session.add(reference)
    session.flush()
    if intent.replacement_node_id is not None:
        assert destination is not None
        version = repository.add_version(
            session,
            destination.id,
            RepositoryVersionCreate(
                object_id=record.id,
                object_reference_id=reference.id,
                product=product,
                external_reference=external,
                source_type="repository_upload",
                source_id=str(intent.id),
                source_status="available",
                provenance={"original_filename": intent.filename},
            ),
            principal,
            actor,
        )
        node = session.get(RepositoryNode, version.node_id)
        assert node is not None
    else:
        if destination is None:
            access = RepositoryAccessEnvelope(
                read_action="platform.repository.read",
                write_action="platform.repository.write",
            )
            parent_id = None
        else:
            access = RepositoryAccessEnvelope(
                read_action=destination.required_read_action,
                write_action=destination.required_write_action,
                site_id=destination.site_id,
            )
            parent_id = destination.id
        node = repository.publish_reference(
            session,
            record,
            reference,
            ObjectReferenceCreate(
                product=product,
                external_reference=external,
                idempotency_key=external,
                repository=RepositoryPublication(
                    filename=intent.filename,
                    parent_id=parent_id,
                    source_type="repository_upload",
                    source_id=str(intent.id),
                    source_status="available",
                    module="repository",
                    classification=record.classification,
                    access=access,
                    provenance={"original_filename": intent.filename},
                ),
            ),
            actor,
        )
        assert node is not None
    intent.completed_at = datetime.now(UTC)
    return _node_response(node, session, principal, rich)


@router.delete("/repository/uploads/{object_id}", status_code=status.HTTP_204_NO_CONTENT)
def abort_repository_upload(
    object_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    actor: Annotated[User, Depends(get_current_user)],
    objects: Annotated[ObjectService, Depends(get_objects)],
) -> Response:
    intent = session.scalar(
        select(RepositoryUpload).where(
            RepositoryUpload.object_id == object_id,
            RepositoryUpload.created_by == actor.id,
            RepositoryUpload.completed_at.is_(None),
        )
    )
    if intent is None:
        raise Problem(404, "repository_upload_not_found", "The Repository upload was not found")
    record = session.get(StoredObject, object_id)
    if record is not None and record.status == ObjectStatus.pending:
        objects.abort(session, object_id, principal)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/repository/versions/{version_id}/tabular-preview",
    response_model=TabularPreviewResponse,
)
def tabular_preview(
    version_id: UUID,
    command: TabularPreviewRequest,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    rich: Annotated[RichRepositoryService, Depends(get_rich_repository)],
) -> TabularPreviewResponse:
    return rich.tabular_preview(session, version_id, command, principal)


@router.post(
    "/repository/versions/{version_id}/conversions",
    response_model=RepositoryJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_conversion(
    version_id: UUID,
    command: RepositoryConversionCreate,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    actor: Annotated[User, Depends(get_current_user)],
    rich: Annotated[RichRepositoryService, Depends(get_rich_repository)],
) -> RepositoryJobResponse:
    return RepositoryJobResponse.model_validate(
        rich.create_conversion(session, version_id, command, principal, actor)
    )


@router.get("/repository/jobs/{job_id}", response_model=RepositoryJobResponse)
def get_repository_job(
    job_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    rich: Annotated[RichRepositoryService, Depends(get_rich_repository)],
) -> RepositoryJobResponse:
    return RepositoryJobResponse.model_validate(rich.get_job(session, job_id, principal))


@router.get(
    "/repository/versions/{version_id}/annotations",
    response_model=list[PdfAnnotationResponse],
)
def list_pdf_annotations(
    version_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    rich: Annotated[RichRepositoryService, Depends(get_rich_repository)],
) -> list[PdfAnnotationResponse]:
    return [
        PdfAnnotationResponse.model_validate(item)
        for item in rich.list_annotations(session, version_id, principal)
    ]


@router.post(
    "/repository/versions/{version_id}/annotations",
    response_model=PdfAnnotationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_pdf_annotation(
    version_id: UUID,
    command: PdfAnnotationCreate,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    actor: Annotated[User, Depends(get_current_user)],
    rich: Annotated[RichRepositoryService, Depends(get_rich_repository)],
) -> PdfAnnotationResponse:
    value = rich.create_annotation(session, version_id, command, principal, actor)
    response.headers["ETag"] = f'"{value.revision}"'
    return PdfAnnotationResponse.model_validate(value)


@router.patch("/repository/annotations/{annotation_id}", response_model=PdfAnnotationResponse)
def patch_pdf_annotation(
    annotation_id: UUID,
    command: PdfAnnotationPatch,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    actor: Annotated[User, Depends(get_current_user)],
    rich: Annotated[RichRepositoryService, Depends(get_rich_repository)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> PdfAnnotationResponse:
    value = rich.patch_annotation(
        session, annotation_id, command, _expected_revision(if_match), principal, actor
    )
    response.headers["ETag"] = f'"{value.revision}"'
    return PdfAnnotationResponse.model_validate(value)


@router.delete("/repository/annotations/{annotation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pdf_annotation(
    annotation_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    actor: Annotated[User, Depends(get_current_user)],
    rich: Annotated[RichRepositoryService, Depends(get_rich_repository)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Response:
    rich.delete_annotation(session, annotation_id, _expected_revision(if_match), principal, actor)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/repository/versions/{version_id}/annotated-exports",
    response_model=RepositoryJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_annotated_export(
    version_id: UUID,
    command: RepositoryExportCreate,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    actor: Annotated[User, Depends(get_current_user)],
    rich: Annotated[RichRepositoryService, Depends(get_rich_repository)],
) -> RepositoryJobResponse:
    return RepositoryJobResponse.model_validate(
        rich.create_annotated_export(session, version_id, command, principal, actor)
    )


@router.post(
    "/repository/versions/{version_id}/office-sessions",
    response_model=OfficeSessionResponse,
)
def create_office_session(
    version_id: UUID,
    command: OfficeSessionCreate,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    actor: Annotated[User, Depends(get_current_user)],
    rich: Annotated[RichRepositoryService, Depends(get_rich_repository)],
) -> OfficeSessionResponse:
    return rich.office_session(session, version_id, command.mode, principal, actor)


@router.get("/integrations/onlyoffice/content/{session_id}", include_in_schema=False)
def onlyoffice_content(
    session_id: UUID,
    token: Annotated[str, Query(min_length=1)],
    session: Annotated[Session, Depends(get_session)],
    rich: Annotated[RichRepositoryService, Depends(get_rich_repository)],
) -> StreamingResponse:
    data, media_type, filename = rich.office_content(session, session_id, token)
    safe_name = filename.replace('"', "")
    return StreamingResponse(
        io.BytesIO(data),
        media_type=media_type,
        headers={
            "Content-Disposition": f'inline; filename="{safe_name}"',
            "Content-Length": str(len(data)),
            "Accept-Ranges": "bytes",
        },
    )


@router.post("/integrations/onlyoffice/callback/{session_id}", include_in_schema=False)
async def onlyoffice_callback(
    session_id: UUID,
    request: Request,
    token: Annotated[str, Query(min_length=1)],
    session: Annotated[Session, Depends(get_session)],
    rich: Annotated[RichRepositoryService, Depends(get_rich_repository)],
) -> dict[str, int]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise Problem(422, "office_callback_invalid", "The Office callback is invalid")
    return rich.office_callback(session, session_id, token, payload)
