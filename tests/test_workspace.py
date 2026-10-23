import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from aurinara_platform.auth import InvalidTokenError, Principal
from aurinara_platform.config import Settings
from aurinara_platform.main import create_app
from aurinara_platform.models import (
    Role,
    RoleAssignment,
    User,
    Workspace,
    WorkspaceLaunchGrant,
    WorkspaceProxySession,
)
from aurinara_platform.storage import ObjectFacts
from aurinara_platform.workspace_runtime import RuntimeInspection, RuntimeStart


class WorkspaceVerifier:
    def verify(self, token: str) -> Principal:
        principals = {
            "platform-token": Principal("issuer", "platform-user", audience="platform-client"),
            "artifact-token": Principal("issuer", "artifact-user", audience="artifact-client"),
            "denied-token": Principal("issuer", "denied-user", audience="artifact-client"),
            "service-token": Principal("issuer", "artifact-user", audience=None),
            "unknown-product-token": Principal("issuer", "artifact-user", audience="other-client"),
        }
        if token not in principals:
            raise InvalidTokenError("invalid")
        return principals[token]


class NullStorage:
    def create_multipart(self, key, media_type, sha256, retention_until=None):
        return "upload"

    def presign_upload_part(self, key, upload_id, part_number):
        return "https://upload.test"

    def complete_multipart(self, key, upload_id, parts): ...

    def abort_multipart(self, key, upload_id): ...

    def facts(self, key):
        return ObjectFacts(1, "0" * 64, "application/octet-stream")

    def presign_download(self, key):
        return "https://download.test"

    def delete(self, key): ...


class FakeWorkspaceRuntime:
    def __init__(self) -> None:
        self.states: dict[str, RuntimeInspection] = {}
        self.starts = 0
        self.stops = 0

    def start(self, *, workspace_id, owner_user_id, product, generation, storage_prefix):
        self.starts += 1
        assert storage_prefix == f"workspaces/{product}/{owner_user_id}/"
        task_arn = f"task:{workspace_id}:{generation}"
        target = "http://10.0.1.20:8080"
        self.states[task_arn] = RuntimeInspection("running", target)
        return RuntimeStart(
            task_arn,
            "running",
            target,
            None,
            hashlib.sha256(b"callback-token").hexdigest(),
        )

    def inspect(self, task_arn):
        return self.states[task_arn]

    def stop(self, task_arn):
        self.stops += 1
        self.states[task_arn] = RuntimeInspection("stopped")

    def remove_environment_file(self, key):
        del key


def _client(session_factory, *, environment: str = "test"):
    settings_values = dict(
        environment="test",
        workspace_enabled=True,
        workspace_product_audiences=(
            "platform=platform-client;aurinara=artifact-client;aurinara-edc=edc-client"
        ),
        workspace_public_base_url="https://platform.example.test",
    )
    if environment == "staging":
        settings_values.update(
            environment="staging",
            workspace_runtime_mode="ecs",
            workspace_cluster="cluster",
            workspace_task_definition="task-definition",
            workspace_subnet_ids="subnet-1",
            workspace_security_group_id="sg-1",
            workspace_bucket="workspace-bucket",
            workspace_sync_role_arn="arn:aws:iam::123456789012:role/workspace-sync",
            workspace_callback_secret="test-callback-secret",
        )
    settings = Settings(**settings_values)
    runtime = FakeWorkspaceRuntime()
    with session_factory.begin() as session:
        platform_user = User(issuer="issuer", subject="platform-user")
        artifact_user = User(issuer="issuer", subject="artifact-user")
        denied_user = User(issuer="issuer", subject="denied-user")
        platform_role = Role(product="platform", name="Platform User", status="active")
        artifact_role = Role(product="aurinara", name="Aurinara User", status="active")
        session.add_all(
            [platform_user, artifact_user, denied_user, platform_role, artifact_role]
        )
        session.flush()
        session.add_all(
            [
                RoleAssignment(
                    user_id=platform_user.id,
                    role_id=platform_role.id,
                    assigned_by=platform_user.id,
                ),
                RoleAssignment(
                    user_id=artifact_user.id,
                    role_id=artifact_role.id,
                    assigned_by=artifact_user.id,
                ),
            ]
        )
    app = create_app(
        settings,
        session_factory=session_factory,
        token_verifier=WorkspaceVerifier(),
        object_storage=NullStorage(),
        workspace_runtime=runtime,
    )
    return TestClient(app), runtime


def test_workspace_is_one_click_idempotent_and_public_state_is_sanitized(session_factory) -> None:
    client, runtime = _client(session_factory)
    headers = {"Authorization": "Bearer artifact-token"}

    initial = client.get("/v1/workspaces/current", headers=headers)
    assert initial.status_code == 200
    assert initial.json()["status"] == "not_created"

    first = client.post("/v1/workspaces/current/start", headers=headers)
    second = client.post("/v1/workspaces/current/start", headers=headers)
    assert first.status_code == second.status_code == 202
    assert first.json()["status"] == second.json()["status"] == "running"
    assert runtime.starts == 1
    assert set(first.json()) == {
        "workspace_id",
        "product",
        "status",
        "sync_state",
        "usage_bytes",
        "quota_bytes",
        "session_expires_at",
        "last_synced_at",
        "failure_code",
        "can_open",
        "can_stop",
    }


def test_workspace_requires_an_active_role_for_the_token_product(session_factory) -> None:
    client, _ = _client(session_factory)
    response = client.get(
        "/v1/workspaces/current", headers={"Authorization": "Bearer denied-token"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "workspace_access_denied"


def test_workspace_rejects_service_and_unknown_product_tokens(session_factory) -> None:
    client, _ = _client(session_factory)
    service = client.get(
        "/v1/workspaces/current", headers={"Authorization": "Bearer service-token"}
    )
    unknown = client.get(
        "/v1/workspaces/current",
        headers={"Authorization": "Bearer unknown-product-token"},
    )
    assert service.status_code == unknown.status_code == 403
    assert service.json()["error"]["code"] == "workspace_user_token_required"
    assert unknown.json()["error"]["code"] == "workspace_product_unknown"


def test_launch_grant_is_single_use_and_creates_only_a_hashed_proxy_session(
    session_factory,
) -> None:
    client, _ = _client(session_factory, environment="staging")
    headers = {"Authorization": "Bearer artifact-token"}
    started = client.post("/v1/workspaces/current/start", headers=headers).json()
    launched = client.post("/v1/workspaces/current/launch", headers=headers)
    assert launched.status_code == 200
    launch_url = launched.json()["url"]
    grant = parse_qs(urlparse(launch_url).query)["grant"][0]
    assert "artifact-token" not in launch_url

    login = client.get(
        f"/v1/workspaces/{started['workspace_id']}/ide-login?grant={grant}",
        follow_redirects=False,
    )
    assert login.status_code == 303
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "Secure" in login.headers["set-cookie"]
    assert "SameSite=lax" in login.headers["set-cookie"]
    assert f"Path=/v1/workspaces/{started['workspace_id']}/ide" in login.headers["set-cookie"]
    replay = client.get(
        f"/v1/workspaces/{started['workspace_id']}/ide-login?grant={grant}",
        follow_redirects=False,
    )
    assert replay.status_code == 403

    with session_factory() as session:
        stored_grant = session.scalar(select(WorkspaceLaunchGrant))
        proxy_session = session.scalar(select(WorkspaceProxySession))
        assert stored_grant is not None and stored_grant.token_sha256 != grant
        assert proxy_session is not None
        assert len(proxy_session.token_sha256) == 64


def test_expired_workspace_is_stopped_and_reported_expired(session_factory) -> None:
    client, runtime = _client(session_factory)
    headers = {"Authorization": "Bearer artifact-token"}
    workspace_id = client.post("/v1/workspaces/current/start", headers=headers).json()[
        "workspace_id"
    ]
    with session_factory.begin() as session:
        workspace = session.get(Workspace, UUID(workspace_id))
        assert workspace is not None
        workspace.session_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    state = client.get("/v1/workspaces/current", headers=headers)
    assert state.status_code == 200
    assert state.json()["status"] == "expired"
    assert runtime.stops == 1


def test_quota_heartbeat_blocks_normal_stop_until_usage_is_reduced(session_factory) -> None:
    client, runtime = _client(session_factory)
    headers = {"Authorization": "Bearer artifact-token"}
    workspace_id = client.post("/v1/workspaces/current/start", headers=headers).json()[
        "workspace_id"
    ]
    over_quota = client.post(
        f"/v1/internal/workspaces/{workspace_id}/sync",
        headers={"X-Workspace-Token": "callback-token"},
        json={"sync_state": "quota_exceeded", "usage_bytes": 5 * 1024**3 + 1},
    )
    assert over_quota.status_code == 204
    blocked = client.post("/v1/workspaces/current/stop", headers=headers)
    assert blocked.status_code == 409
    assert runtime.stops == 0

    recovered = client.post(
        f"/v1/internal/workspaces/{workspace_id}/sync",
        headers={"X-Workspace-Token": "callback-token"},
        json={"sync_state": "synced", "usage_bytes": 1024},
    )
    assert recovered.status_code == 204
    stopped = client.post("/v1/workspaces/current/stop", headers=headers)
    assert stopped.status_code == 202
    assert stopped.json()["status"] == "stopping"
    assert runtime.stops == 1


def test_workspace_records_are_isolated_by_product(session_factory) -> None:
    client, runtime = _client(session_factory)
    artifact = client.post(
        "/v1/workspaces/current/start", headers={"Authorization": "Bearer artifact-token"}
    )
    platform = client.post(
        "/v1/workspaces/current/start", headers={"Authorization": "Bearer platform-token"}
    )
    assert artifact.json()["product"] == "aurinara"
    assert platform.json()["product"] == "platform"
    assert artifact.json()["workspace_id"] != platform.json()["workspace_id"]
    assert runtime.starts == 2
    with session_factory() as session:
        assert len(session.scalars(select(Workspace)).all()) == 2
