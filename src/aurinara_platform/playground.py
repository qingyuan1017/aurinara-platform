"""Central Playground catalog, session ownership, and AgentCore transport."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

import boto3
import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aurinara_platform.auth import Principal
from aurinara_platform.config import Settings
from aurinara_platform.models import (
    PlaygroundAgent,
    PlaygroundApprovalEvent,
    PlaygroundConversation,
)
from aurinara_platform.playground_schemas import AgentCreate, AgentPatch, MessageResponse
from aurinara_platform.problems import Problem
from aurinara_platform.services import AuthorizationService


def product_for_principal(settings: Settings, principal: Principal) -> str:
    if principal.audience is None:
        raise Problem(403, "playground_user_token_required", "A user token is required")
    product = settings.playground_audience_products.get(principal.audience)
    if product is None:
        raise Problem(403, "playground_product_unknown", "The token product is not configured")
    return product


class PlaygroundService:
    def __init__(self, settings: Settings, authorization: AuthorizationService) -> None:
        self._settings = settings
        self._authorization = authorization

    def list_agents(self, session: Session, principal: Principal) -> list[PlaygroundAgent]:
        product = product_for_principal(self._settings, principal)
        agents = session.scalars(
            select(PlaygroundAgent)
            .where(PlaygroundAgent.enabled.is_(True))
            .order_by(PlaygroundAgent.name, PlaygroundAgent.id)
        ).all()
        return [agent for agent in agents if product in agent.product_codes]

    def list_admin_agents(
        self, session: Session, principal: Principal
    ) -> list[PlaygroundAgent]:
        self._require_admin(session, principal)
        return list(session.scalars(select(PlaygroundAgent).order_by(PlaygroundAgent.name)).all())

    def create_agent(
        self, session: Session, command: AgentCreate, principal: Principal
    ) -> PlaygroundAgent:
        self._require_admin(session, principal)
        agent = PlaygroundAgent(**command.model_dump())
        session.add(agent)
        try:
            session.flush()
        except IntegrityError as error:
            raise Problem(
                409,
                "agent_runtime_conflict",
                "The AgentCore runtime ARN is already registered",
            ) from error
        return agent

    def patch_agent(
        self,
        session: Session,
        agent_id: UUID,
        command: AgentPatch,
        principal: Principal,
        expected_revision: int,
    ) -> PlaygroundAgent:
        self._require_admin(session, principal)
        agent = session.get(PlaygroundAgent, agent_id)
        if agent is None:
            raise Problem(404, "agent_not_found", "The agent was not found")
        if agent.revision != expected_revision:
            raise Problem(412, "revision_mismatch", "The agent has changed")
        for name, value in command.model_dump(exclude_unset=True).items():
            setattr(agent, name, value)
        agent.revision += 1
        try:
            session.flush()
        except IntegrityError as error:
            raise Problem(
                409,
                "agent_runtime_conflict",
                "The AgentCore runtime ARN is already registered",
            ) from error
        return agent

    def create_conversation(
        self,
        session: Session,
        command_agent_id: UUID,
        principal: Principal,
        correlation_id: str,
        title: str | None = None,
    ) -> PlaygroundConversation:
        product = product_for_principal(self._settings, principal)
        agent = session.get(PlaygroundAgent, command_agent_id)
        if agent is None or not agent.enabled or product not in agent.product_codes:
            raise Problem(404, "agent_not_found", "The agent was not found")
        conversation = PlaygroundConversation(
            agent_id=agent.id,
            product=product,
            principal_issuer=principal.issuer,
            principal_subject=principal.subject,
            runtime_session_id=str(uuid4()),
            title=title.strip() if title and title.strip() else None,
            state="active",
            correlation_id=correlation_id,
        )
        session.add(conversation)
        session.flush()
        return conversation

    def owned_conversation(
        self, session: Session, conversation_id: UUID, principal: Principal
    ) -> PlaygroundConversation:
        conversation = session.get(PlaygroundConversation, conversation_id)
        if conversation is None:
            raise Problem(404, "conversation_not_found", "The conversation was not found")
        if (conversation.principal_issuer, conversation.principal_subject) != (
            principal.issuer,
            principal.subject,
        ):
            raise Problem(404, "conversation_not_found", "The conversation was not found")
        if conversation.product != product_for_principal(self._settings, principal):
            raise Problem(404, "conversation_not_found", "The conversation was not found")
        return conversation

    def list_conversations(
        self,
        session: Session,
        principal: Principal,
        agent_id: UUID | None = None,
        include_archived: bool = False,
    ) -> list[PlaygroundConversation]:
        product = product_for_principal(self._settings, principal)
        statement = select(PlaygroundConversation).where(
            PlaygroundConversation.principal_issuer == principal.issuer,
            PlaygroundConversation.principal_subject == principal.subject,
            PlaygroundConversation.product == product,
        )
        if agent_id is not None:
            statement = statement.where(PlaygroundConversation.agent_id == agent_id)
        if not include_archived:
            statement = statement.where(PlaygroundConversation.state != "archived")
        return list(
            session.scalars(
                statement.order_by(
                    PlaygroundConversation.updated_at.desc(),
                    PlaygroundConversation.id.desc(),
                )
            ).all()
        )

    def update_conversation(
        self,
        session: Session,
        conversation_id: UUID,
        principal: Principal,
        *,
        title: str | None = None,
        state: str | None = None,
    ) -> PlaygroundConversation:
        conversation = self.owned_conversation(session, conversation_id, principal)
        if title is not None:
            conversation.title = title.strip() or None
        if state is not None:
            conversation.state = state
            if state == "archived":
                conversation.pending_interrupt_id = None
                conversation.pending_tool_name = None
                conversation.pending_arguments_sha256 = None
        session.flush()
        return conversation

    def begin_message(
        self, session: Session, conversation_id: UUID, principal: Principal
    ) -> PlaygroundConversation:
        conversation = self.owned_conversation(session, conversation_id, principal)
        if conversation.state == "failed":
            conversation.state = "active"
            session.flush()
        if conversation.state != "active":
            raise Problem(409, "conversation_not_active", "Start a new conversation")
        if not conversation.agent.enabled:
            raise Problem(409, "agent_disabled", "The selected agent is disabled")
        return conversation

    def record_user_turn(
        self, session: Session, conversation: PlaygroundConversation, message: str
    ) -> None:
        if not conversation.title:
            compact = " ".join(message.split())
            conversation.title = compact if len(compact) <= 60 else f"{compact[:59].rstrip()}…"
        conversation.updated_at = datetime.now(UTC)
        session.flush()

    def record_interrupt(
        self,
        session: Session,
        conversation: PlaygroundConversation,
        interrupt_id: str,
        tool_name: str,
        arguments: object,
    ) -> None:
        conversation.state = "awaiting_approval"
        conversation.pending_interrupt_id = interrupt_id
        conversation.pending_tool_name = tool_name
        conversation.pending_arguments_sha256 = hashlib.sha256(
            json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        session.flush()

    def approve(
        self,
        session: Session,
        conversation_id: UUID,
        interrupt_id: str,
        decision: str,
        principal: Principal,
    ) -> PlaygroundConversation:
        conversation = self.owned_conversation(session, conversation_id, principal)
        if (
            conversation.state != "awaiting_approval"
            or conversation.pending_interrupt_id != interrupt_id
        ):
            raise Problem(409, "approval_not_pending", "This approval is no longer pending")
        session.add(
            PlaygroundApprovalEvent(
                conversation_id=conversation.id,
                interrupt_id=interrupt_id,
                tool_name=conversation.pending_tool_name or "unknown",
                arguments_sha256=(
                    conversation.pending_arguments_sha256 or hashlib.sha256(b"").hexdigest()
                ),
                decision=decision,
                principal_issuer=principal.issuer,
                principal_subject=principal.subject,
                correlation_id=conversation.correlation_id,
            )
        )
        conversation.state = "active"
        conversation.pending_interrupt_id = None
        conversation.pending_tool_name = None
        conversation.pending_arguments_sha256 = None
        session.flush()
        return conversation

    def close(
        self, session: Session, conversation_id: UUID, principal: Principal
    ) -> PlaygroundConversation:
        conversation = self.owned_conversation(session, conversation_id, principal)
        conversation.state = "archived"
        conversation.pending_interrupt_id = None
        conversation.pending_tool_name = None
        conversation.pending_arguments_sha256 = None
        session.flush()
        return conversation

    def _require_admin(self, session: Session, principal: Principal) -> None:
        self._authorization.require(
            session,
            principal,
            "platform.role.manage",
            product="platform",
            resource_type="playground_agent",
        )


class AgentCoreMemoryClient:
    """Read the durable conversational events written by AgentCore Memory."""

    def __init__(
        self,
        client_factory: object | None = None,
        control_client_factory: object | None = None,
    ) -> None:
        self._client_factory = client_factory or (
            lambda region: boto3.client("bedrock-agentcore", region_name=region)
        )
        self._control_client_factory = control_client_factory or (
            lambda region: boto3.client("bedrock-agentcore-control", region_name=region)
        )
        self._resolved_memory_ids: dict[str, str] = {}

    def list_messages(
        self, agent: PlaygroundAgent, conversation: PlaygroundConversation
    ) -> list[MessageResponse]:
        parts = agent.runtime_arn.split(":", 5)
        region = parts[3] if len(parts) == 6 else ""
        memory_id = self._memory_id(agent, region)
        client = self._client_factory(region)
        request: dict[str, object] = {
            "memoryId": memory_id,
            "actorId": conversation.principal_subject,
            "sessionId": conversation.runtime_session_id,
            "includePayloads": True,
        }
        events: list[dict[str, Any]] = []
        try:
            while True:
                response = client.list_events(**request)
                for key in ("events", "items", "messages", "sessionMessages"):
                    items = response.get(key)
                    if isinstance(items, list):
                        events.extend(item for item in items if isinstance(item, dict))
                        break
                next_token = response.get("nextToken")
                if not next_token:
                    break
                request["nextToken"] = next_token
        except Exception as error:
            raise Problem(
                502,
                "agent_memory_unavailable",
                "AgentCore Memory history is unavailable",
            ) from error

        messages = [
            _memory_message(conversation.id, conversation.runtime_session_id, index, item)
            for index, item in enumerate(events)
        ]
        return sorted((item for item in messages if item.content), key=lambda item: item.created_at)

    def _memory_id(self, agent: PlaygroundAgent, region: str) -> str:
        if agent.memory_id:
            return agent.memory_id
        cached = self._resolved_memory_ids.get(agent.runtime_arn)
        if cached:
            return cached

        client = self._control_client_factory(region)
        request: dict[str, object] = {}
        unmanaged: list[str] = []
        try:
            while True:
                response = client.list_memories(**request)
                for memory in response.get("memories", []):
                    memory_id = memory.get("id")
                    if not memory_id:
                        continue
                    if memory.get("managedByResourceArn") == agent.runtime_arn:
                        self._resolved_memory_ids[agent.runtime_arn] = memory_id
                        return memory_id
                    if not memory.get("managedByResourceArn") and memory.get("status") == "ACTIVE":
                        unmanaged.append(memory_id)
                next_token = response.get("nextToken")
                if not next_token:
                    break
                request["nextToken"] = next_token
        except Exception as error:
            raise Problem(
                502,
                "agent_memory_unavailable",
                "AgentCore Memory configuration is unavailable",
            ) from error

        if len(unmanaged) == 1:
            self._resolved_memory_ids[agent.runtime_arn] = unmanaged[0]
            return unmanaged[0]
        raise Problem(
            409,
            "agent_memory_not_configured",
            "Set this agent's AgentCore memory ID in the Platform catalog",
        )


def _memory_message(
    conversation_id: UUID,
    runtime_session_id: str,
    index: int,
    event: dict[str, Any],
) -> MessageResponse:
    created_at = (
        event.get("createdAt")
        or event.get("timestamp")
        or event.get("eventTime")
        or event.get("eventTimestamp")
    )
    role = event.get("role") or event.get("speaker") or event.get("type")
    content = ""
    for key in ("content", "text", "message", "inputText", "outputText"):
        if isinstance(event.get(key), str):
            content = str(event[key])
            break
    for payload in event.get("payload") or []:
        if not isinstance(payload, dict):
            continue
        conversational = payload.get("conversational")
        if not isinstance(conversational, dict):
            continue
        role = conversational.get("role") or role
        value = conversational.get("content")
        if isinstance(value, dict) and isinstance(value.get("text"), str):
            content = _unwrap_memory_text(value["text"])
            break
    normalized_role = str(role or "assistant").lower()
    if normalized_role in {"human", "user_input"}:
        normalized_role = "user"
    elif normalized_role in {"agent", "ai", "model"}:
        normalized_role = "assistant"
    return MessageResponse(
        id=str(
            event.get("messageId")
            or event.get("id")
            or event.get("eventId")
            or f"{runtime_session_id}:{index}"
        ),
        conversation_id=conversation_id,
        role=normalized_role,
        content=content,
        created_at=_memory_datetime(created_at),
    )


def _unwrap_memory_text(value: str) -> str:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return value
    message = payload.get("message") if isinstance(payload, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        return "".join(
            str(item.get("text", "")) for item in content if isinstance(item, dict)
        )
    return value


def _memory_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)


class AgentCoreRuntimeClient:
    def __init__(self, timeout_seconds: int, client_factory: object | None = None) -> None:
        self._timeout_seconds = timeout_seconds
        self._client_factory = client_factory or httpx.AsyncClient

    @staticmethod
    def _url(agent: PlaygroundAgent) -> str:
        parts = agent.runtime_arn.split(":", 5)
        region = parts[3] if len(parts) == 6 else ""
        encoded_arn = quote(agent.runtime_arn, safe="")
        qualifier = quote(agent.runtime_qualifier or "DEFAULT", safe="")
        return (
            f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/"
            f"{encoded_arn}/invocations?qualifier={qualifier}"
        )

    async def stream(
        self,
        agent: PlaygroundAgent,
        runtime_session_id: str,
        payload: dict[str, object],
        bearer_token: str,
        correlation_id: str,
    ) -> AsyncIterator[dict[str, object]]:
        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": runtime_session_id,
            "X-Correlation-ID": correlation_id,
        }
        async with self._client_factory(timeout=self._timeout_seconds) as client:
            try:
                async with client.stream(
                    "POST", self._url(agent), headers=headers, json=payload
                ) as response:
                    response.raise_for_status()
                    async for item in _iter_sse_json(response):
                        yield item
            except httpx.TimeoutException as error:
                raise Problem(504, "agent_timeout", "The agent did not respond in time") from error
            except httpx.HTTPStatusError as error:
                status_code = (
                    502 if error.response.status_code >= 500 else error.response.status_code
                )
                raise Problem(
                    status_code,
                    "agent_runtime_error",
                    "The agent runtime rejected the request",
                ) from error
            except httpx.HTTPError as error:
                raise Problem(
                    502, "agent_unavailable", "The agent runtime is unavailable"
                ) from error


async def _iter_sse_json(response: httpx.Response) -> AsyncIterator[dict[str, object]]:
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if line == "":
            if data_lines:
                raw = "\n".join(data_lines)
                data_lines.clear()
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield value
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        try:
            value = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            return
        if isinstance(value, dict):
            yield value


def normalize_runtime_event(event: dict[str, object]) -> list[tuple[str, dict[str, object]]]:
    playground = event.get("playground")
    if isinstance(playground, dict) and isinstance(playground.get("type"), str):
        kind = str(playground["type"])
        data = {key: value for key, value in playground.items() if key != "type"}
        return [(kind, data)]

    native = event.get("event")
    if not isinstance(native, dict):
        return []
    delta = native.get("contentBlockDelta")
    if isinstance(delta, dict):
        inner = delta.get("delta")
        if isinstance(inner, dict) and isinstance(inner.get("text"), str):
            return [("text_delta", {"text": inner["text"]})]
    start = native.get("contentBlockStart")
    if isinstance(start, dict) and isinstance(start.get("start"), dict):
        tool = start["start"].get("toolUse")
        if isinstance(tool, dict):
            return [
                (
                    "tool_status",
                    {
                        "tool_name": str(tool.get("name", "tool")),
                        "tool_use_id": str(tool.get("toolUseId", "")),
                        "status": "running",
                    },
                )
            ]
    if "messageStop" in native:
        return [("completed", {"status": "completed"})]
    return []


def encode_sse(event: str, data: dict[str, object]) -> bytes:
    encoded = json.dumps(data, separators=(",", ":"), default=str)
    return f"event: {event}\ndata: {encoded}\n\n".encode()
