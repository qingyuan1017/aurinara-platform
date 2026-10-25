"""Public and internal workspace transport contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

WorkspaceStatus = Literal[
    "not_created", "pending", "running", "stopping", "stopped", "failed", "expired"
]
WorkspaceSyncState = Literal["pending", "syncing", "synced", "quota_exceeded", "failed"]


class WorkspaceState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID | None
    product: str
    status: WorkspaceStatus
    sync_state: WorkspaceSyncState
    usage_bytes: int = Field(ge=0)
    quota_bytes: int = Field(gt=0)
    session_expires_at: datetime | None
    last_synced_at: datetime | None
    failure_code: str | None
    can_open: bool
    can_stop: bool


class WorkspaceLaunch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    expires_at: datetime


class WorkspaceSyncHeartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sync_state: WorkspaceSyncState
    usage_bytes: int = Field(ge=0)
    synced_at: datetime | None = None
    failure_code: Literal["sync_failed", "quota_exceeded"] | None = None


class WorkspaceRepositoryEntryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    path: str
    study_id: UUID
    node_id: UUID
    version_id: UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str
    writable: bool


class WorkspaceRepositoryManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[WorkspaceRepositoryEntryResponse]


class WorkspaceStaticRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    callback_token: str
    repository_url: str
