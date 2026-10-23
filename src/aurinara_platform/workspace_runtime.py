"""Workspace runtime adapters. Product services never depend on ECS directly."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from aurinara_platform.config import Settings

RuntimeStatus = Literal["pending", "running", "stopping", "stopped", "failed"]


class WorkspaceRuntimeError(RuntimeError):
    """Raised when a workspace runtime cannot complete an operation."""


class WorkspaceCapacityError(WorkspaceRuntimeError):
    """Raised when the configured global runtime limit has been reached."""


@dataclass(frozen=True, slots=True)
class RuntimeStart:
    task_arn: str
    status: RuntimeStatus
    target_url: str | None
    environment_file_key: str | None
    callback_token_sha256: str


@dataclass(frozen=True, slots=True)
class RuntimeInspection:
    status: RuntimeStatus
    target_url: str | None = None
    failure_code: str | None = None


class WorkspaceRuntime(Protocol):
    def start(
        self,
        *,
        workspace_id: UUID,
        owner_user_id: UUID,
        product: str,
        generation: int,
        storage_prefix: str,
    ) -> RuntimeStart: ...

    def inspect(self, task_arn: str) -> RuntimeInspection: ...

    def stop(self, task_arn: str) -> None: ...

    def remove_environment_file(self, key: str) -> None: ...


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def callback_token(settings: Settings, workspace_id: UUID, generation: int) -> str:
    payload = f"{workspace_id}:{generation}".encode()
    return hmac.new(
        settings.workspace_callback_secret.get_secret_value().encode(), payload, hashlib.sha256
    ).hexdigest()


class DisabledWorkspaceRuntime:
    def start(self, **kwargs: object) -> RuntimeStart:
        del kwargs
        raise WorkspaceRuntimeError("workspace runtime is disabled")

    def inspect(self, task_arn: str) -> RuntimeInspection:
        del task_arn
        return RuntimeInspection("failed", failure_code="runtime_disabled")

    def stop(self, task_arn: str) -> None:
        del task_arn

    def remove_environment_file(self, key: str) -> None:
        del key


class StaticWorkspaceRuntime:
    """Local-only adapter for loopback/Compose code-server instances."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._targets = settings.workspace_static_target_map
        self._states: dict[str, RuntimeInspection] = {}

    def start(
        self,
        *,
        workspace_id: UUID,
        owner_user_id: UUID,
        product: str,
        generation: int,
        storage_prefix: str,
    ) -> RuntimeStart:
        del owner_user_id, storage_prefix
        target = self._targets.get(product)
        if target is None:
            raise WorkspaceRuntimeError("workspace product target is not configured")
        task_arn = f"static:{workspace_id}:{generation}"
        self._states[task_arn] = RuntimeInspection("running", target_url=target)
        token = callback_token(self._settings, workspace_id, generation)
        return RuntimeStart(task_arn, "running", target, None, _sha256(token))

    def inspect(self, task_arn: str) -> RuntimeInspection:
        return self._states.get(task_arn, RuntimeInspection("stopped"))

    def stop(self, task_arn: str) -> None:
        self._states[task_arn] = RuntimeInspection("stopped")

    def remove_environment_file(self, key: str) -> None:
        del key


class EcsWorkspaceRuntime:
    """Launch private Fargate tasks with credentials isolated to the sync sidecar."""

    def __init__(self, settings: Settings, *, client_factory=boto3.client) -> None:
        self._settings = settings
        self._ecs = client_factory("ecs", region_name=settings.workspace_region)
        self._s3 = client_factory("s3", region_name=settings.workspace_region)
        self._sts = client_factory("sts", region_name=settings.workspace_region)

    def _active_count(self) -> int:
        task_arns: set[str] = set()
        for desired_status in ("RUNNING", "PENDING"):
            response = self._ecs.list_tasks(
                cluster=self._settings.workspace_cluster,
                family=self._settings.workspace_task_family,
                desiredStatus=desired_status,
            )
            task_arns.update(response.get("taskArns", []))
        return len(task_arns)

    def start(
        self,
        *,
        workspace_id: UUID,
        owner_user_id: UUID,
        product: str,
        generation: int,
        storage_prefix: str,
    ) -> RuntimeStart:
        try:
            return self._start(
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
                product=product,
                generation=generation,
                storage_prefix=storage_prefix,
            )
        except WorkspaceRuntimeError:
            raise
        except (BotoCoreError, ClientError, KeyError) as error:
            raise WorkspaceRuntimeError("AWS workspace runtime request failed") from error

    def _start(
        self,
        *,
        workspace_id: UUID,
        owner_user_id: UUID,
        product: str,
        generation: int,
        storage_prefix: str,
    ) -> RuntimeStart:
        del owner_user_id
        if self._active_count() >= self._settings.workspace_max_running:
            raise WorkspaceCapacityError("workspace capacity has been reached")

        scoped_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:ListBucket"],
                    "Resource": [f"arn:aws:s3:::{self._settings.workspace_bucket}"],
                    "Condition": {
                        "StringLike": {"s3:prefix": [storage_prefix, f"{storage_prefix}*"]}
                    },
                },
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                    "Resource": [
                        f"arn:aws:s3:::{self._settings.workspace_bucket}/{storage_prefix}*"
                    ],
                },
            ],
        }
        credentials = self._sts.assume_role(
            RoleArn=self._settings.workspace_sync_role_arn,
            RoleSessionName=f"workspace-{workspace_id.hex[:24]}",
            DurationSeconds=self._settings.workspace_sync_credential_seconds,
            Policy=json.dumps(scoped_policy, separators=(",", ":")),
        )["Credentials"]
        raw_callback_token = callback_token(self._settings, workspace_id, generation)
        environment_key = f"session-env/{workspace_id}/{generation}.env"
        environment = {
            "AWS_ACCESS_KEY_ID": credentials["AccessKeyId"],
            "AWS_SECRET_ACCESS_KEY": credentials["SecretAccessKey"],
            "AWS_SESSION_TOKEN": credentials["SessionToken"],
            "AWS_DEFAULT_REGION": self._settings.workspace_region,
            "WORKSPACE_ID": str(workspace_id),
            "WORKSPACE_BUCKET": self._settings.workspace_bucket,
            "WORKSPACE_PREFIX": storage_prefix,
            "WORKSPACE_QUOTA_BYTES": str(self._settings.workspace_quota_bytes),
            "WORKSPACE_SYNC_INTERVAL_SECONDS": str(
                self._settings.workspace_sync_interval_seconds
            ),
            "WORKSPACE_CALLBACK_URL": (
                f"{self._settings.workspace_callback_base_url.rstrip('/')}"
                f"{self._settings.api_prefix}/internal/workspaces/{workspace_id}/sync"
            ),
            "WORKSPACE_CALLBACK_TOKEN": raw_callback_token,
        }
        body = "".join(f"{name}={value}\n" for name, value in environment.items()).encode()
        self._s3.put_object(
            Bucket=self._settings.workspace_bucket,
            Key=environment_key,
            Body=body,
            ContentType="text/plain",
        )

        response = self._ecs.run_task(
            cluster=self._settings.workspace_cluster,
            taskDefinition=self._settings.workspace_task_definition,
            launchType="FARGATE",
            platformVersion="1.4.0",
            count=1,
            clientToken=f"{workspace_id.hex}-{generation}",
            startedBy=f"workspace-{workspace_id.hex[:24]}",
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": self._settings.workspace_subnet_id_list,
                    "securityGroups": [self._settings.workspace_security_group_id],
                    "assignPublicIp": "DISABLED",
                }
            },
            overrides={
                "containerOverrides": [
                    {
                        "name": "workspace-sync",
                        "environmentFiles": [
                            {
                                "type": "s3",
                                "value": (
                                    f"arn:aws:s3:::{self._settings.workspace_bucket}/"
                                    f"{environment_key}"
                                ),
                            }
                        ],
                    },
                    {
                        "name": "workspace-ide",
                        "environment": [
                            {"name": "WORKSPACE_ID", "value": str(workspace_id)},
                            {
                                "name": "WORKSPACE_SESSION_SECONDS",
                                "value": str(self._settings.workspace_session_seconds),
                            },
                        ],
                    },
                ]
            },
            tags=[
                {"key": "aurinara:workspace-id", "value": str(workspace_id)},
                {"key": "aurinara:product", "value": product},
            ],
        )
        failures = response.get("failures", [])
        tasks = response.get("tasks", [])
        if failures or not tasks:
            raise WorkspaceRuntimeError("ECS did not accept the workspace task")
        return RuntimeStart(
            task_arn=tasks[0]["taskArn"],
            status="pending",
            target_url=None,
            environment_file_key=environment_key,
            callback_token_sha256=_sha256(raw_callback_token),
        )

    def inspect(self, task_arn: str) -> RuntimeInspection:
        try:
            response = self._ecs.describe_tasks(
                cluster=self._settings.workspace_cluster, tasks=[task_arn]
            )
        except (BotoCoreError, ClientError) as error:
            raise WorkspaceRuntimeError("AWS workspace inspection failed") from error
        tasks = response.get("tasks", [])
        if not tasks:
            return RuntimeInspection("failed", failure_code="runtime_missing")
        task = tasks[0]
        status = task.get("lastStatus", "UNKNOWN")
        if status == "RUNNING":
            private_ip = next(
                (
                    detail.get("value")
                    for attachment in task.get("attachments", [])
                    for detail in attachment.get("details", [])
                    if detail.get("name") == "privateIPv4Address"
                ),
                None,
            )
            if private_ip:
                return RuntimeInspection("running", target_url=f"http://{private_ip}:8080")
            return RuntimeInspection("pending")
        if status in {"PROVISIONING", "PENDING", "ACTIVATING"}:
            return RuntimeInspection("pending")
        if status in {"DEACTIVATING", "STOPPING", "DEPROVISIONING"}:
            return RuntimeInspection("stopping")
        if status == "STOPPED":
            containers = task.get("containers", [])
            exit_codes = [item.get("exitCode") for item in containers]
            failed = any(code not in {None, 0, 143} for code in exit_codes)
            return RuntimeInspection(
                "failed" if failed else "stopped",
                failure_code="runtime_failed" if failed else None,
            )
        return RuntimeInspection("failed", failure_code="runtime_unknown")

    def stop(self, task_arn: str) -> None:
        try:
            self._ecs.stop_task(
                cluster=self._settings.workspace_cluster,
                task=task_arn,
                reason="Aurinara workspace stop requested",
            )
        except (BotoCoreError, ClientError) as error:
            raise WorkspaceRuntimeError("AWS workspace stop failed") from error

    def remove_environment_file(self, key: str) -> None:
        try:
            self._s3.delete_object(Bucket=self._settings.workspace_bucket, Key=key)
        except (BotoCoreError, ClientError) as error:
            raise WorkspaceRuntimeError("workspace environment cleanup failed") from error


def create_workspace_runtime(settings: Settings) -> WorkspaceRuntime:
    if settings.workspace_runtime_mode == "ecs":
        return EcsWorkspaceRuntime(settings)
    if settings.workspace_runtime_mode == "static":
        return StaticWorkspaceRuntime(settings)
    return DisabledWorkspaceRuntime()
