"""HTTP boundary for the central Playground."""

from collections.abc import AsyncIterator
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from aurinara_platform.api import bearer, get_principal, get_session
from aurinara_platform.auth import Principal
from aurinara_platform.playground import (
    AgentCoreMemoryClient,
    AgentCoreRuntimeClient,
    PlaygroundService,
    encode_sse,
    normalize_runtime_event,
)
from aurinara_platform.playground_schemas import (
    AgentAdminResponse,
    AgentCreate,
    AgentPatch,
    AgentSummary,
    ApprovalDecision,
    ConversationCreate,
    ConversationPatch,
    ConversationResponse,
    MessageCreate,
    MessageResponse,
)
from aurinara_platform.problems import Problem

router = APIRouter(prefix="/playground", tags=["playground"])


def get_playground(request: Request) -> PlaygroundService:
    return cast(PlaygroundService, request.app.state.playground_service)


def get_runtime(request: Request) -> AgentCoreRuntimeClient:
    return cast(AgentCoreRuntimeClient, request.app.state.playground_runtime)


def get_memory(request: Request) -> AgentCoreMemoryClient:
    return cast(AgentCoreMemoryClient, request.app.state.playground_memory)


def token_value(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
) -> str:
    if credentials is None:
        raise Problem(401, "invalid_token", "A bearer token is required")
    return credentials.credentials


@router.get("/agents", response_model=list[AgentSummary])
def list_agents(
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[PlaygroundService, Depends(get_playground)],
) -> list[AgentSummary]:
    return [AgentSummary.model_validate(item) for item in service.list_agents(session, principal)]


@router.post("/conversations", response_model=ConversationResponse, status_code=201)
def create_conversation(
    command: ConversationCreate,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[PlaygroundService, Depends(get_playground)],
) -> ConversationResponse:
    value = service.create_conversation(
        session, command.agent_id, principal, request.state.correlation_id, command.title
    )
    return ConversationResponse.model_validate(value, from_attributes=True)


@router.get("/conversations", response_model=list[ConversationResponse])
def list_conversations(
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[PlaygroundService, Depends(get_playground)],
    agent_id: UUID | None = None,
    include_archived: bool = False,
) -> list[ConversationResponse]:
    return [
        ConversationResponse.model_validate(item)
        for item in service.list_conversations(
            session, principal, agent_id=agent_id, include_archived=include_archived
        )
    ]


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
def get_conversation(
    conversation_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[PlaygroundService, Depends(get_playground)],
) -> ConversationResponse:
    return ConversationResponse.model_validate(
        service.owned_conversation(session, conversation_id, principal)
    )


@router.patch("/conversations/{conversation_id}", response_model=ConversationResponse)
def update_conversation(
    conversation_id: UUID,
    command: ConversationPatch,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[PlaygroundService, Depends(get_playground)],
) -> ConversationResponse:
    return ConversationResponse.model_validate(
        service.update_conversation(
            session,
            conversation_id,
            principal,
            title=command.title,
            state=command.state,
        )
    )


@router.get(
    "/conversations/{conversation_id}/messages", response_model=list[MessageResponse]
)
def list_messages(
    conversation_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[PlaygroundService, Depends(get_playground)],
    memory: Annotated[AgentCoreMemoryClient, Depends(get_memory)],
) -> list[MessageResponse]:
    conversation = service.owned_conversation(session, conversation_id, principal)
    return memory.list_messages(conversation.agent, conversation)


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: UUID,
    command: MessageCreate,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    access_token: Annotated[str, Depends(token_value)],
    service: Annotated[PlaygroundService, Depends(get_playground)],
    runtime: Annotated[AgentCoreRuntimeClient, Depends(get_runtime)],
) -> StreamingResponse:
    conversation = service.begin_message(session, conversation_id, principal)
    service.record_user_turn(session, conversation, command.message)
    session.commit()

    async def events() -> AsyncIterator[bytes]:
        completed = False
        try:
            async for raw in runtime.stream(
                conversation.agent,
                conversation.runtime_session_id,
                {"prompt": command.message, "correlation_id": conversation.correlation_id},
                access_token,
                conversation.correlation_id,
            ):
                for event_name, data in normalize_runtime_event(raw):
                    if event_name == "approval_required":
                        service.record_interrupt(
                            session,
                            conversation,
                            str(data.get("interrupt_id", "")),
                            str(data.get("tool_name", "tool")),
                            data.get("arguments", {}),
                        )
                        session.commit()
                    if event_name == "completed":
                        completed = True
                    yield encode_sse(event_name, data)
            if not completed and conversation.state == "active":
                yield encode_sse("completed", {"status": "completed"})
        except Problem as error:
            conversation.state = "failed"
            session.commit()
            yield encode_sse(
                "error",
                {
                    "code": error.code,
                    "message": error.detail,
                    "retryable": error.status_code >= 500,
                },
            )
        except Exception:
            conversation.state = "failed"
            session.commit()
            yield encode_sse(
                "error",
                {
                    "code": "agent_stream_failed",
                    "message": "The agent stream failed",
                    "retryable": True,
                },
            )

    return StreamingResponse(events(), media_type="text/event-stream")


@router.post("/conversations/{conversation_id}/approvals/{interrupt_id}")
async def decide_approval(
    conversation_id: UUID,
    interrupt_id: str,
    command: ApprovalDecision,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    access_token: Annotated[str, Depends(token_value)],
    service: Annotated[PlaygroundService, Depends(get_playground)],
    runtime: Annotated[AgentCoreRuntimeClient, Depends(get_runtime)],
) -> StreamingResponse:
    conversation = service.approve(
        session, conversation_id, interrupt_id, command.decision, principal
    )
    session.commit()

    async def events() -> AsyncIterator[bytes]:
        completed = False
        try:
            payload = {
                "interrupt_responses": [
                    {"interrupt_id": interrupt_id, "response": command.decision}
                ],
                "correlation_id": conversation.correlation_id,
            }
            async for raw in runtime.stream(
                conversation.agent,
                conversation.runtime_session_id,
                payload,
                access_token,
                conversation.correlation_id,
            ):
                for event_name, data in normalize_runtime_event(raw):
                    if event_name == "approval_required":
                        service.record_interrupt(
                            session,
                            conversation,
                            str(data.get("interrupt_id", "")),
                            str(data.get("tool_name", "tool")),
                            data.get("arguments", {}),
                        )
                        session.commit()
                    if event_name == "completed":
                        completed = True
                    yield encode_sse(event_name, data)
            if not completed and conversation.state == "active":
                yield encode_sse("completed", {"status": "completed"})
        except Problem as error:
            conversation.state = "failed"
            session.commit()
            yield encode_sse(
                "error",
                {
                    "code": error.code,
                    "message": error.detail,
                    "retryable": error.status_code >= 500,
                },
            )
        except Exception:
            conversation.state = "failed"
            session.commit()
            yield encode_sse(
                "error",
                {
                    "code": "agent_stream_failed",
                    "message": "The agent stream failed",
                    "retryable": True,
                },
            )

    return StreamingResponse(events(), media_type="text/event-stream")


@router.post("/conversations/{conversation_id}/close", response_model=ConversationResponse)
def close_conversation(
    conversation_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[PlaygroundService, Depends(get_playground)],
) -> ConversationResponse:
    value = service.close(session, conversation_id, principal)
    return ConversationResponse.model_validate(value, from_attributes=True)


@router.get("/admin/agents", response_model=list[AgentAdminResponse])
def list_admin_agents(
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[PlaygroundService, Depends(get_playground)],
) -> list[AgentAdminResponse]:
    return [
        AgentAdminResponse.model_validate(item)
        for item in service.list_admin_agents(session, principal)
    ]


@router.post("/admin/agents", response_model=AgentAdminResponse, status_code=201)
def create_agent(
    command: AgentCreate,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[PlaygroundService, Depends(get_playground)],
) -> AgentAdminResponse:
    return AgentAdminResponse.model_validate(service.create_agent(session, command, principal))


@router.patch("/admin/agents/{agent_id}", response_model=AgentAdminResponse)
def patch_agent(
    agent_id: UUID,
    command: AgentPatch,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[PlaygroundService, Depends(get_playground)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> AgentAdminResponse:
    if if_match is None:
        raise Problem(428, "if_match_required", "If-Match is required")
    try:
        revision = int(if_match.strip('"'))
    except ValueError as error:
        raise Problem(400, "invalid_if_match", "If-Match must contain a revision") from error
    return AgentAdminResponse.model_validate(
        service.patch_agent(session, agent_id, command, principal, revision)
    )
