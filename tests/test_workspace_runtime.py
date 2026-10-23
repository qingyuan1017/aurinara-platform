import hashlib
import json
from uuid import uuid4

from aurinara_platform.config import Settings
from aurinara_platform.workspace_runtime import EcsWorkspaceRuntime


class FakeEcs:
    def __init__(self) -> None:
        self.run: dict[str, object] | None = None

    def list_tasks(self, **kwargs):
        del kwargs
        return {"taskArns": []}

    def run_task(self, **kwargs):
        self.run = kwargs
        return {"tasks": [{"taskArn": "arn:aws:ecs:task/workspace-1"}], "failures": []}


class FakeS3:
    def __init__(self) -> None:
        self.put: dict[str, object] | None = None

    def put_object(self, **kwargs):
        self.put = kwargs


class FakeSts:
    def __init__(self) -> None:
        self.assume: dict[str, object] | None = None

    def assume_role(self, **kwargs):
        self.assume = kwargs
        return {"Credentials": {
            "AccessKeyId": "temporary-access",
            "SecretAccessKey": "temporary-secret",
            "SessionToken": "temporary-session",
        }}


def test_ecs_runtime_isolates_sync_credentials_and_exact_storage_prefix() -> None:
    clients = {"ecs": FakeEcs(), "s3": FakeS3(), "sts": FakeSts()}
    settings = Settings(
        environment="test",
        workspace_enabled=True,
        workspace_runtime_mode="ecs",
        workspace_cluster="cluster",
        workspace_task_definition="workspace:1",
        workspace_task_family="workspace",
        workspace_subnet_ids="subnet-a,subnet-b",
        workspace_security_group_id="sg-workspace",
        workspace_bucket="workspace-bucket",
        workspace_sync_role_arn="arn:aws:iam::123456789012:role/workspace-sync",
        workspace_callback_secret="callback-secret",
    )
    runtime = EcsWorkspaceRuntime(settings, client_factory=lambda name, **_: clients[name])
    workspace_id = uuid4()
    owner_id = uuid4()
    prefix = f"workspaces/aurinara/{owner_id}/"

    started = runtime.start(
        workspace_id=workspace_id,
        owner_user_id=owner_id,
        product="aurinara",
        generation=1,
        storage_prefix=prefix,
    )

    assume = clients["sts"].assume
    assert assume is not None
    policy = json.loads(str(assume["Policy"]))
    assert policy["Statement"][0]["Condition"]["StringLike"]["s3:prefix"] == [
        prefix,
        f"{prefix}*",
    ]
    assert policy["Statement"][1]["Resource"] == [
        f"arn:aws:s3:::workspace-bucket/{prefix}*"
    ]

    put = clients["s3"].put
    assert put is not None
    environment = bytes(put["Body"]).decode()
    assert "AWS_ACCESS_KEY_ID=temporary-access" in environment
    assert "WORKSPACE_CALLBACK_TOKEN=" in environment
    raw_callback = next(
        line.split("=", 1)[1]
        for line in environment.splitlines()
        if line.startswith("WORKSPACE_CALLBACK_TOKEN=")
    )
    assert started.callback_token_sha256 == hashlib.sha256(raw_callback.encode()).hexdigest()

    run = clients["ecs"].run
    assert run is not None
    overrides = {item["name"]: item for item in run["overrides"]["containerOverrides"]}
    assert "environmentFiles" in overrides["workspace-sync"]
    assert "environmentFiles" not in overrides["workspace-ide"]
    ide_environment = {item["name"] for item in overrides["workspace-ide"]["environment"]}
    assert ide_environment == {"WORKSPACE_ID", "WORKSPACE_SESSION_SECONDS"}
    assert run["clientToken"] == f"{workspace_id.hex}-1"
