"""HTTP contract for the central study Repository."""

from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy.orm import Session

from aurinara_platform.api import get_current_user, get_principal, get_session
from aurinara_platform.auth import Principal
from aurinara_platform.models import RepositoryFileVersion, RepositoryNode, StoredObject, User
from aurinara_platform.problems import Problem
from aurinara_platform.repository import RepositoryService
from aurinara_platform.schemas import (
    DownloadResponse,
    RepositoryFolderCreate,
    RepositoryNodePage,
    RepositoryNodePatch,
    RepositoryNodeResponse,
    RepositoryPublication,
    RepositoryStatusPatch,
    RepositoryVersionBindingResponse,
    RepositoryVersionCreate,
    RepositoryVersionResponse,
)

router = APIRouter(tags=["repository"])


def get_repository(request: Request) -> RepositoryService:
    return cast(RepositoryService, request.app.state.repository_service)


def _expected_revision(if_match: str | None) -> int:
    if if_match is None:
        raise Problem(428, "if_match_required", "If-Match is required")
    try:
        return int(if_match.strip('"'))
    except ValueError as error:
        raise Problem(400, "invalid_if_match", "If-Match must contain a revision") from error


def _node_response(node: RepositoryNode) -> RepositoryNodeResponse:
    return RepositoryNodeResponse.model_validate(node)


def _version_response(
    version: RepositoryFileVersion, stored_object: StoredObject
) -> RepositoryVersionResponse:
    return RepositoryVersionResponse(
        id=version.id,
        node_id=version.node_id,
        version_number=version.version_number,
        object_id=version.object_id,
        object_reference_id=version.object_reference_id,
        sha256=stored_object.sha256 or "",
        size_bytes=stored_object.size_bytes or 0,
        media_type=stored_object.media_type,
        created_by=version.created_by,
        created_at=version.created_at,
    )


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
    return RepositoryNodePage(
        items=[_node_response(item) for item in items], next_cursor=next_cursor
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
) -> RepositoryNodeResponse:
    node = service.get_node(session, node_id, principal)
    response.headers["ETag"] = f'"{node.revision}"'
    return _node_response(node)


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
) -> RepositoryVersionResponse:
    version = service.add_version(session, node_id, command, principal, actor)
    stored_object = session.get(StoredObject, version.object_id)
    assert stored_object is not None
    return _version_response(version, stored_object)


@router.get(
    "/repository/nodes/{node_id}/versions",
    response_model=list[RepositoryVersionResponse],
)
def list_repository_versions(
    node_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[RepositoryService, Depends(get_repository)],
) -> list[RepositoryVersionResponse]:
    return [
        _version_response(version, stored_object)
        for version, stored_object in service.list_versions(session, node_id, principal)
    ]


@router.get("/repository/versions/{version_id}", response_model=RepositoryVersionBindingResponse)
def get_repository_version(
    version_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[RepositoryService, Depends(get_repository)],
) -> RepositoryVersionBindingResponse:
    node, version, stored_object = service.resolve_version(session, version_id, principal)
    base = _version_response(version, stored_object)
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
