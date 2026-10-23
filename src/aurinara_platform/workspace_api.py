"""HTTP boundary for personal product workspaces."""

from datetime import UTC, datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, WebSocket, status
from sqlalchemy.orm import Session, sessionmaker
from starlette.responses import RedirectResponse

from aurinara_platform.api import get_current_user, get_principal, get_session
from aurinara_platform.auth import Principal
from aurinara_platform.config import Settings
from aurinara_platform.models import User
from aurinara_platform.problems import Problem
from aurinara_platform.workspace import WorkspaceProxyAuthorizer, WorkspaceService
from aurinara_platform.workspace_proxy import (
    WORKSPACE_SESSION_COOKIE,
    proxy_http_request,
    proxy_websocket,
)
from aurinara_platform.workspace_schemas import (
    WorkspaceLaunch,
    WorkspaceState,
    WorkspaceSyncHeartbeat,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])
internal_router = APIRouter(prefix="/internal/workspaces", include_in_schema=False)


def get_workspace_service(request: Request) -> WorkspaceService:
    return cast(WorkspaceService, request.app.state.workspace_service)


def _proxy_authorizer(request: Request) -> WorkspaceProxyAuthorizer:
    return cast(WorkspaceProxyAuthorizer, request.app.state.workspace_proxy_authorizer)


@router.get("/current", response_model=WorkspaceState)
def current_workspace(
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[WorkspaceService, Depends(get_workspace_service)],
) -> WorkspaceState:
    return service.current(session, principal, user)


@router.post("/current/start", response_model=WorkspaceState, status_code=status.HTTP_202_ACCEPTED)
def start_workspace(
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[WorkspaceService, Depends(get_workspace_service)],
) -> WorkspaceState:
    return service.start(session, principal, user)


@router.post("/current/stop", response_model=WorkspaceState, status_code=status.HTTP_202_ACCEPTED)
def stop_workspace(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[WorkspaceService, Depends(get_workspace_service)],
) -> WorkspaceState:
    state = service.stop(session, principal, user)
    if state.workspace_id:
        _proxy_authorizer(request).invalidate(state.workspace_id)
    return state


@router.post("/current/launch", response_model=WorkspaceLaunch)
def launch_workspace(
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[WorkspaceService, Depends(get_workspace_service)],
) -> WorkspaceLaunch:
    return service.launch(session, principal, user)


@router.get("/{workspace_id}/ide-login", include_in_schema=False)
def workspace_ide_login(
    workspace_id: UUID,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    service: Annotated[WorkspaceService, Depends(get_workspace_service)],
    grant: Annotated[str, Query(min_length=20, max_length=256)],
) -> RedirectResponse:
    raw_session, expires_at = service.consume_launch_grant(session, workspace_id, grant)
    settings = cast(Settings, request.app.state.settings)
    proxy_base = f"{settings.api_prefix}/workspaces/{workspace_id}/ide"
    max_age = max(0, int((expires_at - datetime.now(UTC)).total_seconds()))
    response = RedirectResponse(f"{proxy_base}/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        WORKSPACE_SESSION_COOKIE,
        raw_session,
        max_age=max_age,
        httponly=True,
        secure=settings.environment not in {"development", "test"},
        samesite="lax",
        path=proxy_base,
    )
    return response


def _proxy_target(request: Request, session: Session, workspace_id: UUID) -> str:
    raw_token = request.cookies.get(WORKSPACE_SESSION_COOKIE)
    if not raw_token:
        raise Problem(403, "workspace_session_invalid", "The workspace session is invalid")
    return _proxy_authorizer(request).authorize(session, workspace_id, raw_token).target_url


@router.api_route(
    "/{workspace_id}/ide",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
@router.api_route(
    "/{workspace_id}/ide/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def workspace_ide_proxy(
    workspace_id: UUID,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    path: str = "",
):
    target = _proxy_target(request, session, workspace_id)
    settings = cast(Settings, request.app.state.settings)
    public_path = f"{settings.api_prefix}/workspaces/{workspace_id}/ide"
    return await proxy_http_request(request, target, path, public_path)


@router.websocket("/{workspace_id}/ide")
@router.websocket("/{workspace_id}/ide/{path:path}")
async def workspace_ide_websocket(
    websocket: WebSocket, workspace_id: UUID, path: str = ""
) -> None:
    raw_token = websocket.cookies.get(WORKSPACE_SESSION_COOKIE)
    if not raw_token:
        await websocket.close(code=1008)
        return
    factory = cast(sessionmaker[Session], websocket.app.state.session_factory)
    authorizer = cast(
        WorkspaceProxyAuthorizer, websocket.app.state.workspace_proxy_authorizer
    )
    try:
        with factory() as session:
            target = authorizer.authorize(session, workspace_id, raw_token).target_url
    except Problem:
        await websocket.close(code=1008)
        return
    await proxy_websocket(websocket, target, path)


@internal_router.post("/{workspace_id}/sync", status_code=status.HTTP_204_NO_CONTENT)
def workspace_sync_heartbeat(
    workspace_id: UUID,
    command: WorkspaceSyncHeartbeat,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    service: Annotated[WorkspaceService, Depends(get_workspace_service)],
) -> Response:
    raw_token = request.headers.get("X-Workspace-Token")
    if not raw_token:
        raise Problem(403, "workspace_callback_invalid", "The callback token is invalid")
    service.heartbeat(session, workspace_id, raw_token, command)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
