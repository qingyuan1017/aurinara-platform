"""Control-plane HTTP API."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session, sessionmaker

from aurinara_platform.auth import InvalidTokenError, Principal, TokenVerifier
from aurinara_platform.config import Settings
from aurinara_platform.models import User
from aurinara_platform.problems import Problem
from aurinara_platform.schemas import (
    AuthorizationBatchRequest,
    AuthorizationBatchResponse,
    AuthorizationDecisionRequest,
    AuthorizationDecisionResponse,
    DownloadResponse,
    MembershipReplace,
    MembershipResponse,
    ObjectReferenceCreate,
    ObjectReferenceResponse,
    ObjectResponse,
    StudyCreate,
    StudyPatch,
    StudyResponse,
    UploadFinalize,
    UploadSessionCreate,
    UploadSessionResponse,
    UserCreate,
    UserPatch,
    UserResponse,
)
from aurinara_platform.services import (
    AuthorizationService,
    MembershipService,
    ObjectService,
    StudyService,
    UserService,
    resolve_user,
)

router = APIRouter()
bearer = HTTPBearer(auto_error=False)


def get_session(request: Request) -> Iterator[Session]:
    factory = cast(sessionmaker[Session], request.app.state.session_factory)
    with factory() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
) -> Principal:
    if credentials is None:
        raise Problem(401, "invalid_token", "A bearer token is required")
    verifier = cast(TokenVerifier, request.app.state.token_verifier)
    try:
        return verifier.verify(credentials.credentials)
    except InvalidTokenError as error:
        raise Problem(401, "invalid_token", str(error)) from error


def get_current_user(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
) -> User:
    settings = cast(Settings, request.app.state.settings)
    return resolve_user(session, principal, settings)


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_authorization(request: Request) -> AuthorizationService:
    return cast(AuthorizationService, request.app.state.authorization_service)


def get_studies(request: Request) -> StudyService:
    return cast(StudyService, request.app.state.study_service)


def get_memberships(request: Request) -> MembershipService:
    return cast(MembershipService, request.app.state.membership_service)


def get_objects(request: Request) -> ObjectService:
    return cast(ObjectService, request.app.state.object_service)


def get_users(request: Request) -> UserService:
    return cast(UserService, request.app.state.user_service)


@router.get("/me", response_model=UserResponse, tags=["identity"])
def me(user: Annotated[User, Depends(get_current_user)]) -> UserResponse:
    return UserResponse.model_validate(user)


@router.post("/users", response_model=UserResponse, status_code=201, tags=["identity"])
def create_user(
    command: UserCreate,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[UserService, Depends(get_users)],
) -> UserResponse:
    user = service.create(session, command, principal)
    response.headers["ETag"] = f'"{user.revision}"'
    response.headers["Location"] = f"/v1/users/{user.id}"
    return UserResponse.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserResponse, tags=["identity"])
def patch_user(
    user_id: UUID,
    command: UserPatch,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[UserService, Depends(get_users)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> UserResponse:
    if if_match is None:
        raise Problem(428, "if_match_required", "If-Match is required")
    try:
        revision = int(if_match.strip('"'))
    except ValueError as error:
        raise Problem(400, "invalid_if_match", "If-Match must contain a revision") from error
    user = service.patch(session, user_id, command, principal, revision)
    response.headers["ETag"] = f'"{user.revision}"'
    return UserResponse.model_validate(user)


def _require_delegated_decision(
    session: Session,
    caller: Principal,
    service: AuthorizationService,
    commands: list[AuthorizationDecisionRequest],
) -> None:
    if any(
        (caller.issuer, caller.subject) != (item.principal.issuer, item.principal.subject)
        for item in commands
    ):
        service.require(
            session,
            caller,
            "platform.authorization.decide",
            product="platform",
            resource_type="authorization_decision",
        )


@router.post(
    "/authorization/decisions",
    response_model=AuthorizationDecisionResponse,
    tags=["authorization"],
)
def authorize(
    command: AuthorizationDecisionRequest,
    session: Annotated[Session, Depends(get_session)],
    caller: Annotated[Principal, Depends(get_principal)],
    service: Annotated[AuthorizationService, Depends(get_authorization)],
) -> AuthorizationDecisionResponse:
    _require_delegated_decision(session, caller, service, [command])
    return service.decide(session, command)


@router.post(
    "/authorization/decisions:batch",
    response_model=AuthorizationBatchResponse,
    tags=["authorization"],
)
def authorize_batch(
    command: AuthorizationBatchRequest,
    session: Annotated[Session, Depends(get_session)],
    caller: Annotated[Principal, Depends(get_principal)],
    service: Annotated[AuthorizationService, Depends(get_authorization)],
) -> AuthorizationBatchResponse:
    _require_delegated_decision(session, caller, service, command.decisions)
    return AuthorizationBatchResponse(
        decisions=[service.decide(session, item) for item in command.decisions]
    )


@router.post("/studies", response_model=StudyResponse, status_code=201, tags=["studies"])
def create_study(
    command: StudyCreate,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[StudyService, Depends(get_studies)],
) -> StudyResponse:
    study = service.create(session, command, principal, user)
    response.headers["ETag"] = f'"{study.revision}"'
    response.headers["Location"] = f"/v1/studies/{study.id}"
    return StudyResponse.model_validate(study)


@router.get("/studies", response_model=list[StudyResponse], tags=["studies"])
def list_studies(
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[StudyService, Depends(get_studies)],
) -> list[StudyResponse]:
    return [StudyResponse.model_validate(item) for item in service.visible(session, user)]


@router.get("/studies/{study_id}", response_model=StudyResponse, tags=["studies"])
def get_study(
    study_id: UUID,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[StudyService, Depends(get_studies)],
) -> StudyResponse:
    study = service.get(session, study_id, principal)
    response.headers["ETag"] = f'"{study.revision}"'
    return StudyResponse.model_validate(study)


@router.patch("/studies/{study_id}", response_model=StudyResponse, tags=["studies"])
def patch_study(
    study_id: UUID,
    command: StudyPatch,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[StudyService, Depends(get_studies)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> StudyResponse:
    if if_match is None:
        raise Problem(428, "if_match_required", "If-Match is required")
    try:
        revision = int(if_match.strip('"'))
    except ValueError as error:
        raise Problem(400, "invalid_if_match", "If-Match must contain a revision") from error
    study = service.patch(session, study_id, command, principal, revision)
    response.headers["ETag"] = f'"{study.revision}"'
    return StudyResponse.model_validate(study)


def _membership_response(item: object) -> MembershipResponse:
    role = item.role
    return MembershipResponse(
        id=item.id,
        study_id=item.study_id,
        user_id=item.user_id,
        role_id=item.role_id,
        role_name=role.name,
        product=role.product,
        site_id=item.site_id,
        assigned_at=item.assigned_at,
    )


@router.put(
    "/studies/{study_id}/members/{user_id}",
    response_model=list[MembershipResponse],
    tags=["memberships"],
)
def replace_membership(
    study_id: UUID,
    user_id: UUID,
    command: MembershipReplace,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    actor: Annotated[User, Depends(get_current_user)],
    service: Annotated[MembershipService, Depends(get_memberships)],
) -> list[MembershipResponse]:
    items = service.replace(session, study_id, user_id, command, principal, actor)
    return [_membership_response(item) for item in items]


@router.delete("/studies/{study_id}/members/{user_id}", status_code=204, tags=["memberships"])
def remove_membership(
    study_id: UUID,
    user_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[MembershipService, Depends(get_memberships)],
    site_id: Annotated[UUID | None, Query()] = None,
) -> Response:
    service.remove(session, study_id, user_id, principal, site_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/studies/{study_id}/memberships",
    response_model=list[MembershipResponse],
    tags=["memberships"],
)
def list_memberships(
    study_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    authorization: Annotated[AuthorizationService, Depends(get_authorization)],
    service: Annotated[MembershipService, Depends(get_memberships)],
) -> list[MembershipResponse]:
    authorization.require(
        session,
        principal,
        "platform.membership.read",
        product="platform",
        resource_type="membership",
        study_id=study_id,
    )
    return [_membership_response(item) for item in service.list(session, study_id)]


@router.post(
    "/objects/upload-sessions",
    response_model=UploadSessionResponse,
    status_code=201,
    tags=["objects"],
)
def create_upload_session(
    command: UploadSessionCreate,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    actor: Annotated[User, Depends(get_current_user)],
    service: Annotated[ObjectService, Depends(get_objects)],
) -> UploadSessionResponse:
    return service.create_upload(session, command, principal, actor)


@router.post("/objects/{object_id}/finalize", response_model=ObjectResponse, tags=["objects"])
def finalize_upload(
    object_id: UUID,
    command: UploadFinalize,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ObjectService, Depends(get_objects)],
) -> ObjectResponse:
    return ObjectResponse.model_validate(service.finalize(session, object_id, command, principal))


@router.get("/objects/{object_id}", response_model=ObjectResponse, tags=["objects"])
def get_object(
    object_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ObjectService, Depends(get_objects)],
) -> ObjectResponse:
    return ObjectResponse.model_validate(service.metadata(session, object_id, principal))


@router.post("/objects/{object_id}/download", response_model=DownloadResponse, tags=["objects"])
def authorize_download(
    object_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[ObjectService, Depends(get_objects)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DownloadResponse:
    return DownloadResponse(
        url=service.download(session, object_id, principal),
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.object_presign_ttl_seconds),
    )


@router.post(
    "/objects/{object_id}/references",
    response_model=ObjectReferenceResponse,
    status_code=201,
    tags=["objects"],
)
def create_object_reference(
    object_id: UUID,
    command: ObjectReferenceCreate,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    actor: Annotated[User, Depends(get_current_user)],
    service: Annotated[ObjectService, Depends(get_objects)],
) -> ObjectReferenceResponse:
    return ObjectReferenceResponse.model_validate(
        service.add_reference(session, object_id, command, principal, actor)
    )


@router.delete(
    "/object-references/{reference_id}",
    response_model=ObjectReferenceResponse,
    tags=["objects"],
)
def release_object_reference(
    reference_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    actor: Annotated[User, Depends(get_current_user)],
    service: Annotated[ObjectService, Depends(get_objects)],
) -> ObjectReferenceResponse:
    return ObjectReferenceResponse.model_validate(
        service.release_reference(session, reference_id, principal, actor)
    )
