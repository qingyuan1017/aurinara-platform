import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aurinara_platform.auth import Principal
from aurinara_platform.config import Settings
from aurinara_platform.default_access import (
    SYSTEM_ADMINISTRATOR_ROLE_ID,
    ensure_platform_access_defaults,
)
from aurinara_platform.main import create_app
from aurinara_platform.models import (
    PlaygroundAgent,
    PlaygroundApprovalEvent,
    RoleAssignment,
    User,
)
from aurinara_platform.playground import (
    AgentCoreMemoryClient,
    PlaygroundService,
    encode_sse,
    normalize_runtime_event,
    product_for_principal,
)
from aurinara_platform.playground_schemas import AgentCreate, AgentPatch
from aurinara_platform.problems import Problem
from aurinara_platform.services import AuthorizationService


class PlaygroundVerifier:
    def verify(self, token: str) -> Principal:
        if token != "user-token":
            raise AssertionError("unexpected token")
        return Principal("issuer", "person", audience="artifact-client")


class FakeRuntime:
    async def stream(self, agent, runtime_session_id, payload, bearer_token, correlation_id):
        del agent, runtime_session_id, correlation_id
        assert bearer_token == "user-token"
        assert payload["prompt"] == "hello"
        yield {"event": {"contentBlockDelta": {"delta": {"text": "Hello"}}}}
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}


class FakeMemory:
    def list_messages(self, agent, conversation):
        del agent
        from aurinara_platform.playground_schemas import MessageResponse

        return [
            MessageResponse(
                id="memory-event-1",
                conversation_id=conversation.id,
                role="user",
                content="hello",
                created_at=conversation.created_at,
            )
        ]


def _settings() -> Settings:
    return Settings(
        environment="test",
        playground_product_audiences="platform=platform-client;aurinara=artifact-client;aurinara-edc=edc-client",
    )


def _agent(name: str, products: list[str], *, enabled: bool = True) -> PlaygroundAgent:
    return PlaygroundAgent(
        name=name.replace("-", " ").title(),
        product_codes=products,
        runtime_arn=f"arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/{name}",
        memory_id="memory-1",
        runtime_qualifier="DEFAULT",
        enabled=enabled,
    )


def test_catalog_and_conversation_are_product_and_owner_scoped(session) -> None:
    settings = _settings()
    service = PlaygroundService(settings, AuthorizationService(settings))
    artifact_agent = _agent("artifact-agent", ["aurinara"])
    shared_agent = _agent("shared-agent", ["aurinara", "aurinara-edc"])
    edc_agent = _agent("edc-agent", ["aurinara-edc"])
    disabled = _agent("disabled-agent", ["aurinara"], enabled=False)
    session.add_all([artifact_agent, shared_agent, edc_agent, disabled])
    session.flush()

    principal = Principal("issuer", "person-1", audience="artifact-client")
    assert product_for_principal(settings, principal) == "aurinara"
    assert [agent.name for agent in service.list_agents(session, principal)] == [
        "Artifact Agent",
        "Shared Agent",
    ]

    conversation = service.create_conversation(
        session, artifact_agent.id, principal, "correlation-1"
    )
    assert conversation.product == "aurinara"
    with pytest.raises(Problem) as hidden:
        service.owned_conversation(
            session,
            conversation.id,
            Principal("issuer", "person-2", audience="artifact-client"),
        )
    assert hidden.value.status_code == 404

    with pytest.raises(Problem) as cross_product:
        service.owned_conversation(
            session,
            conversation.id,
            Principal("issuer", "person-1", audience="edc-client"),
        )
    assert cross_product.value.status_code == 404

    with pytest.raises(Problem) as unavailable:
        service.create_conversation(session, edc_agent.id, principal, "correlation-2")
    assert unavailable.value.status_code == 404


def test_each_mutation_approval_is_single_use_and_audited(session) -> None:
    settings = _settings()
    service = PlaygroundService(settings, AuthorizationService(settings))
    agent = _agent("edc-agent", ["aurinara-edc"])
    session.add(agent)
    session.flush()
    principal = Principal("issuer", "person", audience="edc-client")
    conversation = service.create_conversation(session, agent.id, principal, "correlation")

    service.record_interrupt(
        session,
        conversation,
        "interrupt-1",
        "update_subject",
        {"subject_id": "S-001", "authorization": "must-not-be-stored"},
    )
    expected_hash = conversation.pending_arguments_sha256
    service.approve(session, conversation.id, "interrupt-1", "approve", principal)

    event = session.scalar(select(PlaygroundApprovalEvent))
    assert event is not None
    assert event.arguments_sha256 == expected_hash
    assert event.decision == "approve"
    assert event.tool_name == "update_subject"
    assert conversation.pending_interrupt_id is None
    with pytest.raises(Problem) as replay:
        service.approve(session, conversation.id, "interrupt-1", "approve", principal)
    assert replay.value.code == "approval_not_pending"


def test_only_platform_admin_can_manage_catalog(session) -> None:
    settings = _settings()
    service = PlaygroundService(settings, AuthorizationService(settings))
    ensure_platform_access_defaults(session)
    admin = User(issuer="issuer", subject="admin", email="admin@example.test")
    regular = User(issuer="issuer", subject="regular", email="regular@example.test")
    session.add_all([admin, regular])
    session.flush()
    session.add(
        RoleAssignment(
            user_id=admin.id,
            role_id=SYSTEM_ADMINISTRATOR_ROLE_ID,
            assigned_by=admin.id,
        )
    )
    session.flush()
    command = AgentCreate(
        name="Protocol Agent",
        product_codes=["aurinara"],
        runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/protocol-agent",
        enabled=True,
    )

    created = service.create_agent(
        session, command, Principal("issuer", "admin", audience="platform-client")
    )
    assert created.revision == 1
    with pytest.raises(Problem) as denied:
        service.create_agent(
            session,
            command.model_copy(
                update={
                    "name": "Second Agent",
                    "runtime_arn": (
                        "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/"
                        "second-agent"
                    ),
                }
            ),
            Principal("issuer", "regular", audience="platform-client"),
        )
    assert denied.value.status_code == 403

    conversation = service.create_conversation(
        session,
        created.id,
        Principal("issuer", "person", audience="artifact-client"),
        "correlation",
    )
    replacement_arn = (
        "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/protocol-agent-v2"
    )
    updated = service.patch_agent(
        session,
        created.id,
        AgentPatch(runtime_arn=replacement_arn),
        Principal("issuer", "admin", audience="platform-client"),
        expected_revision=1,
    )
    assert updated.id == created.id
    assert conversation.agent_id == updated.id

    with pytest.raises(Problem) as duplicate:
        service.create_agent(
            session,
            command.model_copy(
                update={"name": "Duplicate Runtime", "runtime_arn": replacement_arn}
            ),
            Principal("issuer", "admin", audience="platform-client"),
        )
    assert duplicate.value.code == "agent_runtime_conflict"


def test_runtime_events_are_normalized_to_the_small_public_contract() -> None:
    assert normalize_runtime_event(
        {"event": {"contentBlockDelta": {"delta": {"text": "hello"}}}}
    ) == [("text_delta", {"text": "hello"})]
    assert normalize_runtime_event(
        {
            "playground": {
                "type": "approval_required",
                "interrupt_id": "i-1",
                "tool_name": "save_plan",
                "arguments": {"id": 1},
            }
        }
    ) == [
        (
            "approval_required",
            {"interrupt_id": "i-1", "tool_name": "save_plan", "arguments": {"id": 1}},
        )
    ]
    assert encode_sse("completed", {"status": "completed"}).startswith(b"event: completed\n")


def test_agentcore_memory_events_are_exposed_as_conversation_messages(session) -> None:
    class FakeClient:
        def list_events(self, **request):
            assert request == {
                "memoryId": "memory-1",
                "actorId": "person",
                "sessionId": "00000000-0000-0000-0000-000000000001",
                "includePayloads": True,
            }
            return {
                "events": [
                    {
                        "eventId": "event-1",
                        "eventTimestamp": "2026-10-21T12:00:00Z",
                        "payload": [
                            {
                                "conversational": {
                                    "role": "USER",
                                    "content": {
                                        "text": '{"message":{"content":[{"text":"Remember this"}]}}'
                                    },
                                }
                            }
                        ],
                    }
                ]
            }

    class FakeControlClient:
        def list_memories(self, **request):
            assert request == {}
            return {"memories": [{"id": "memory-1", "status": "ACTIVE"}]}

    agent = _agent("memory-agent", ["aurinara"])
    agent.memory_id = None
    session.add(agent)
    session.flush()
    service = PlaygroundService(_settings(), AuthorizationService(_settings()))
    conversation = service.create_conversation(
        session, agent.id, Principal("issuer", "person", audience="artifact-client"), "correlation"
    )
    conversation.runtime_session_id = "00000000-0000-0000-0000-000000000001"
    messages = AgentCoreMemoryClient(
        client_factory=lambda region: FakeClient(),
        control_client_factory=lambda region: FakeControlClient(),
    ).list_messages(agent, conversation)
    assert [(item.role, item.content) for item in messages] == [("user", "Remember this")]


def test_http_boundary_streams_with_the_callers_bearer_token(session_factory) -> None:
    settings = _settings()
    with session_factory.begin() as session:
        agent = _agent("artifact-agent", ["aurinara"])
        session.add(agent)
        session.flush()
        agent_id = agent.id
    app = create_app(
        settings,
        session_factory=session_factory,
        token_verifier=PlaygroundVerifier(),
        object_storage=object(),  # Repository storage is unused by this focused boundary test.
    )
    app.state.playground_runtime = FakeRuntime()
    app.state.playground_memory = FakeMemory()
    client = TestClient(app)
    headers = {"Authorization": "Bearer user-token"}

    listed = client.get("/v1/playground/agents", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [str(agent_id)]
    created = client.post(
        "/v1/playground/conversations", headers=headers, json={"agent_id": str(agent_id)}
    )
    assert created.status_code == 201
    streamed = client.post(
        f"/v1/playground/conversations/{created.json()['id']}/messages",
        headers=headers,
        json={"message": "hello"},
    )
    assert streamed.status_code == 200
    assert "event: text_delta" in streamed.text
    assert 'data: {"text":"Hello"}' in streamed.text
    assert "event: completed" in streamed.text
    conversations = client.get(
        f"/v1/playground/conversations?agent_id={agent_id}", headers=headers
    )
    assert conversations.status_code == 200
    assert conversations.json()[0]["title"] == "hello"
    messages = client.get(
        f"/v1/playground/conversations/{created.json()['id']}/messages", headers=headers
    )
    assert messages.status_code == 200
    assert messages.json()[0]["content"] == "hello"
