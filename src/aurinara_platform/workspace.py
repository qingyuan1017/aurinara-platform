"""Personal product-workspace lifecycle and authorization."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, exists, select, text
from sqlalchemy.orm import Session

from aurinara_platform.auth import Principal
from aurinara_platform.config import Settings
from aurinara_platform.models import (
    Role,
    RoleAssignment,
    User,
    UserStatus,
    Workspace,
    WorkspaceLaunchGrant,
    WorkspaceProxySession,
)
from aurinara_platform.problems import Problem
from aurinara_platform.workspace_runtime import (
    WorkspaceCapacityError,
    WorkspaceRuntime,
    WorkspaceRuntimeError,
    callback_token,
)
from aurinara_platform.workspace_schemas import (
    WorkspaceLaunch,
    WorkspaceState,
    WorkspaceSyncHeartbeat,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ProxyTarget:
    workspace_id: UUID
    target_url: str
    session_expires_at: datetime


@dataclass(frozen=True, slots=True)
class WorkspaceCallbackContext:
    workspace: Workspace
    user: User
    principal: Principal


class WorkspaceService:
    def __init__(self, settings: Settings, runtime: WorkspaceRuntime) -> None:
        self._settings = settings
        self._runtime = runtime

    def product_for(self, principal: Principal) -> str:
        if principal.audience is None:
            raise Problem(403, "workspace_user_token_required", "A user token is required")
        product = self._settings.workspace_audience_products.get(principal.audience)
        if product is None:
            raise Problem(403, "workspace_product_unknown", "The token product is not configured")
        return product

    @staticmethod
    def _has_product_role(session: Session, user_id: UUID, product: str) -> bool:
        return bool(
            session.scalar(
                select(
                    exists().where(
                        RoleAssignment.user_id == user_id,
                        RoleAssignment.role_id == Role.id,
                        Role.product == product,
                        Role.status == "active",
                    )
                )
            )
        )

    def _require_access(
        self, session: Session, principal: Principal, user: User | None = None
    ) -> tuple[User, str]:
        if not self._settings.workspace_enabled:
            raise Problem(503, "workspace_disabled", "Workspace is not enabled")
        product = self.product_for(principal)
        resolved = user or session.scalar(
            select(User).where(
                User.issuer == principal.issuer,
                User.subject == principal.subject,
            )
        )
        if resolved is None or resolved.status != UserStatus.active:
            raise Problem(403, "workspace_access_denied", "Workspace access is not allowed")
        if not self._has_product_role(session, resolved.id, product):
            raise Problem(403, "workspace_access_denied", "Workspace access is not allowed")
        return resolved, product

    def _require_user_access(self, session: Session, user_id: UUID, product: str) -> User:
        user = session.get(User, user_id)
        if (
            user is None
            or user.status != UserStatus.active
            or not self._has_product_role(session, user_id, product)
        ):
            raise Problem(403, "workspace_access_denied", "Workspace access is not allowed")
        return user

    def _find(
        self, session: Session, user_id: UUID, product: str, *, lock: bool = False
    ) -> Workspace | None:
        statement = select(Workspace).where(
            Workspace.owner_user_id == user_id, Workspace.product == product
        )
        if lock:
            statement = statement.with_for_update()
        return session.scalar(statement)

    def _reconcile(self, workspace: Workspace) -> Workspace:
        now = _now()
        expires_at = _aware(workspace.session_expires_at)
        if (
            workspace.status in {"pending", "running", "stopping"}
            and expires_at
            and now >= expires_at
        ):
            if workspace.task_arn:
                with suppress(WorkspaceRuntimeError):
                    self._runtime.stop(workspace.task_arn)
            workspace.status = "expired"
            workspace.stopped_at = now
            workspace.revision += 1
            return workspace
        if workspace.task_arn and workspace.status in {"pending", "running", "stopping"}:
            try:
                inspection = self._runtime.inspect(workspace.task_arn)
            except WorkspaceRuntimeError:
                workspace.status = "failed"
                workspace.failure_code = "runtime_unavailable"
                workspace.revision += 1
                return workspace
            # ECS reports RUNNING before the sync sidecar has restored the
            # persistent volume. Keep a newly-started workspace pending until
            # that first restore attempt reports back.
            reconciled_status = inspection.status
            if (
                inspection.status == "running"
                and workspace.status == "pending"
                and workspace.sync_state == "syncing"
            ):
                reconciled_status = "pending"
            changed = workspace.status != reconciled_status
            workspace.status = reconciled_status
            if inspection.target_url:
                changed = changed or workspace.target_url != inspection.target_url
                workspace.target_url = inspection.target_url
            if inspection.failure_code:
                changed = changed or workspace.failure_code != inspection.failure_code
                workspace.failure_code = inspection.failure_code
            if inspection.status == "running" and workspace.environment_file_key:
                try:
                    self._runtime.remove_environment_file(workspace.environment_file_key)
                except WorkspaceRuntimeError:
                    pass
                else:
                    workspace.environment_file_key = None
                    changed = True
            if workspace.status in {"stopped", "failed"}:
                workspace.stopped_at = workspace.stopped_at or now
                workspace.target_url = None
            if changed:
                workspace.revision += 1
        return workspace

    def _state(self, workspace: Workspace | None, product: str) -> WorkspaceState:
        if workspace is None:
            return WorkspaceState(
                workspace_id=None,
                product=product,
                status="not_created",
                sync_state="pending",
                usage_bytes=0,
                quota_bytes=self._settings.workspace_quota_bytes,
                session_expires_at=None,
                last_synced_at=None,
                failure_code=None,
                can_open=True,
                can_stop=False,
            )
        active = workspace.status in {"pending", "running", "stopping"}
        return WorkspaceState(
            workspace_id=workspace.id,
            product=workspace.product,
            status=workspace.status,
            sync_state=workspace.sync_state,
            usage_bytes=workspace.usage_bytes,
            quota_bytes=self._settings.workspace_quota_bytes,
            session_expires_at=_aware(workspace.session_expires_at),
            last_synced_at=_aware(workspace.last_synced_at),
            failure_code=workspace.failure_code,
            can_open=workspace.status not in {"stopping"},
            can_stop=active and workspace.sync_state != "quota_exceeded",
        )

    def current(
        self, session: Session, principal: Principal, user: User | None = None
    ) -> WorkspaceState:
        resolved, product = self._require_access(session, principal, user)
        workspace = self._find(session, resolved.id, product)
        if workspace:
            self._reconcile(workspace)
        return self._state(workspace, product)

    def start(
        self, session: Session, principal: Principal, user: User | None = None
    ) -> WorkspaceState:
        resolved, product = self._require_access(session, principal, user)
        # Serialize the create-if-missing path for one owner and, on PostgreSQL,
        # serialize capacity checks across all owners. ECS still receives a
        # deterministic clientToken as a second layer of retry protection.
        session.execute(select(User.id).where(User.id == resolved.id).with_for_update())
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            session.execute(text("SELECT pg_advisory_xact_lock(4716230248297292337)"))
        workspace = self._find(session, resolved.id, product, lock=True)
        if workspace is None:
            workspace = Workspace(
                owner_user_id=resolved.id,
                product=product,
                storage_prefix=f"workspaces/{product}/{resolved.id}/",
            )
            session.add(workspace)
            session.flush()
        self._reconcile(workspace)
        if workspace.status in {"pending", "running"}:
            return self._state(workspace, product)
        if workspace.status == "stopping":
            raise Problem(409, "workspace_stopping", "The workspace is still stopping")

        workspace.generation += 1
        workspace.status = "pending"
        workspace.sync_state = "syncing"
        workspace.failure_code = None
        workspace.target_url = None
        workspace.session_started_at = _now()
        workspace.session_expires_at = workspace.session_started_at + timedelta(
            seconds=self._settings.workspace_session_seconds
        )
        workspace.stopped_at = None
        workspace.revision += 1
        session.flush()
        try:
            started = self._runtime.start(
                workspace_id=workspace.id,
                owner_user_id=workspace.owner_user_id,
                product=workspace.product,
                generation=workspace.generation,
                storage_prefix=workspace.storage_prefix,
            )
        except WorkspaceCapacityError as error:
            workspace.status = "stopped"
            workspace.failure_code = "capacity_reached"
            raise Problem(
                429, "workspace_capacity_reached", "Workspace capacity is full"
            ) from error
        except WorkspaceRuntimeError as error:
            workspace.status = "failed"
            workspace.failure_code = "runtime_start_failed"
            raise Problem(
                503, "workspace_runtime_unavailable", "Workspace could not start"
            ) from error
        workspace.task_arn = started.task_arn
        workspace.status = started.status
        workspace.target_url = started.target_url
        workspace.environment_file_key = started.environment_file_key
        workspace.callback_token_sha256 = started.callback_token_sha256
        return self._state(workspace, product)

    def stop(
        self, session: Session, principal: Principal, user: User | None = None
    ) -> WorkspaceState:
        resolved, product = self._require_access(session, principal, user)
        workspace = self._find(session, resolved.id, product, lock=True)
        if workspace is None:
            return self._state(None, product)
        self._reconcile(workspace)
        if workspace.sync_state == "quota_exceeded" and workspace.status in {
            "pending",
            "running",
        }:
            raise Problem(
                409,
                "workspace_quota_exceeded",
                "Reduce workspace usage below the quota before stopping",
            )
        if workspace.status not in {"pending", "running", "stopping"}:
            return self._state(workspace, product)
        if workspace.status != "stopping" and workspace.task_arn:
            try:
                self._runtime.stop(workspace.task_arn)
            except WorkspaceRuntimeError as error:
                raise Problem(
                    503, "workspace_runtime_unavailable", "Workspace could not stop"
                ) from error
        workspace.status = "stopping"
        workspace.revision += 1
        self._revoke_sessions(session, workspace.id)
        return self._state(workspace, product)

    def launch(
        self, session: Session, principal: Principal, user: User | None = None
    ) -> WorkspaceLaunch:
        resolved, product = self._require_access(session, principal, user)
        workspace = self._find(session, resolved.id, product, lock=True)
        if workspace is None:
            raise Problem(409, "workspace_not_running", "The workspace is not running")
        self._reconcile(workspace)
        if workspace.status != "running" or not workspace.target_url:
            raise Problem(409, "workspace_not_running", "The workspace is not running")
        raw_token = secrets.token_urlsafe(32)
        expires_at = min(
            _now() + timedelta(seconds=self._settings.workspace_launch_grant_seconds),
            _aware(workspace.session_expires_at) or datetime.max.replace(tzinfo=UTC),
        )
        session.add(
            WorkspaceLaunchGrant(
                workspace_id=workspace.id,
                token_sha256=_token_hash(raw_token),
                expires_at=expires_at,
            )
        )
        return WorkspaceLaunch(
            url=(
                f"{self._settings.workspace_public_base_url.rstrip('/')}"
                f"{self._settings.api_prefix}/workspaces/{workspace.id}/ide-login"
                f"?grant={raw_token}"
            ),
            expires_at=expires_at,
        )

    def consume_launch_grant(
        self, session: Session, workspace_id: UUID, raw_grant: str
    ) -> tuple[str, datetime]:
        now = _now()
        grant = session.scalar(
            select(WorkspaceLaunchGrant)
            .where(
                WorkspaceLaunchGrant.workspace_id == workspace_id,
                WorkspaceLaunchGrant.token_sha256 == _token_hash(raw_grant),
            )
            .with_for_update()
        )
        if (
            grant is None
            or grant.consumed_at is not None
            or (_aware(grant.expires_at) or now) <= now
        ):
            raise Problem(403, "workspace_launch_grant_invalid", "The launch link is invalid")
        workspace = session.get(Workspace, workspace_id)
        if workspace is None:
            raise Problem(403, "workspace_launch_grant_invalid", "The launch link is invalid")
        self._reconcile(workspace)
        self._require_user_access(session, workspace.owner_user_id, workspace.product)
        if workspace.status != "running" or not workspace.target_url:
            raise Problem(409, "workspace_not_running", "The workspace is not running")
        grant.consumed_at = now
        raw_session = secrets.token_urlsafe(32)
        expires_at = _aware(workspace.session_expires_at) or now
        session.add(
            WorkspaceProxySession(
                workspace_id=workspace.id,
                token_sha256=_token_hash(raw_session),
                expires_at=expires_at,
            )
        )
        return raw_session, expires_at

    def validate_proxy_session(
        self, session: Session, workspace_id: UUID, raw_token: str
    ) -> ProxyTarget:
        now = _now()
        proxy_session = session.scalar(
            select(WorkspaceProxySession).where(
                WorkspaceProxySession.workspace_id == workspace_id,
                WorkspaceProxySession.token_sha256 == _token_hash(raw_token),
                WorkspaceProxySession.revoked_at.is_(None),
            )
        )
        if proxy_session is None or (_aware(proxy_session.expires_at) or now) <= now:
            raise Problem(403, "workspace_session_invalid", "The workspace session is invalid")
        workspace = session.get(Workspace, workspace_id)
        if workspace is None:
            raise Problem(403, "workspace_session_invalid", "The workspace session is invalid")
        self._reconcile(workspace)
        self._require_user_access(session, workspace.owner_user_id, workspace.product)
        if workspace.status != "running" or not workspace.target_url:
            raise Problem(409, "workspace_not_running", "The workspace is not running")
        return ProxyTarget(
            workspace.id, workspace.target_url, _aware(proxy_session.expires_at) or now
        )

    def heartbeat(
        self,
        session: Session,
        workspace_id: UUID,
        raw_token: str,
        heartbeat: WorkspaceSyncHeartbeat,
    ) -> None:
        workspace = self.callback_context(session, workspace_id, raw_token).workspace
        workspace.usage_bytes = heartbeat.usage_bytes
        workspace.sync_state = (
            "quota_exceeded"
            if heartbeat.usage_bytes > self._settings.workspace_quota_bytes
            else heartbeat.sync_state
        )
        workspace.last_synced_at = heartbeat.synced_at or (
            _now() if workspace.sync_state == "synced" else workspace.last_synced_at
        )
        workspace.failure_code = heartbeat.failure_code
        workspace.revision += 1

    def callback_context(
        self, session: Session, workspace_id: UUID, raw_token: str
    ) -> WorkspaceCallbackContext:
        workspace = session.get(Workspace, workspace_id)
        if (
            workspace is None
            or workspace.callback_token_sha256 is None
            or not hmac.compare_digest(workspace.callback_token_sha256, _token_hash(raw_token))
        ):
            raise Problem(403, "workspace_callback_invalid", "The callback token is invalid")
        self._reconcile(workspace)
        user = self._require_user_access(session, workspace.owner_user_id, workspace.product)
        if workspace.status not in {"pending", "running", "stopping"}:
            raise Problem(409, "workspace_not_running", "The workspace is not running")
        return WorkspaceCallbackContext(
            workspace=workspace,
            user=user,
            principal=Principal(
                issuer=user.issuer,
                subject=user.subject,
                email=user.email,
                display_name=user.display_name,
            ),
        )

    def static_callback_registration(
        self, session: Session, product: str, bootstrap_secret: str
    ) -> tuple[Workspace, str]:
        if (
            self._settings.environment != "development"
            or self._settings.workspace_runtime_mode != "static"
            or not hmac.compare_digest(
                bootstrap_secret,
                self._settings.workspace_callback_secret.get_secret_value(),
            )
        ):
            raise Problem(403, "workspace_callback_invalid", "The callback token is invalid")
        workspace = session.scalar(
            select(Workspace)
            .where(
                Workspace.product == product,
                Workspace.status.in_(("pending", "running")),
            )
            .order_by(Workspace.session_started_at.desc())
        )
        if workspace is None:
            raise Problem(404, "workspace_not_running", "The workspace is not running")
        self._require_user_access(session, workspace.owner_user_id, workspace.product)
        raw_token = callback_token(self._settings, workspace.id, workspace.generation)
        if not hmac.compare_digest(
            workspace.callback_token_sha256 or "", _token_hash(raw_token)
        ):
            raise Problem(403, "workspace_callback_invalid", "The callback token is invalid")
        return workspace, raw_token

    @staticmethod
    def _revoke_sessions(session: Session, workspace_id: UUID) -> None:
        now = _now()
        for proxy_session in session.scalars(
            select(WorkspaceProxySession).where(
                WorkspaceProxySession.workspace_id == workspace_id,
                WorkspaceProxySession.revoked_at.is_(None),
            )
        ):
            proxy_session.revoked_at = now
        session.execute(
            delete(WorkspaceLaunchGrant).where(
                WorkspaceLaunchGrant.workspace_id == workspace_id,
                WorkspaceLaunchGrant.consumed_at.is_(None),
            )
        )


class WorkspaceProxyAuthorizer:
    """Bound DB pressure while keeping role/session revocation delay below 15 seconds."""

    def __init__(self, service: WorkspaceService, ttl_seconds: int) -> None:
        self._service = service
        self._ttl = ttl_seconds
        self._cache: dict[tuple[UUID, str], tuple[float, ProxyTarget]] = {}

    def authorize(self, session: Session, workspace_id: UUID, raw_token: str) -> ProxyTarget:
        key = (workspace_id, _token_hash(raw_token))
        cached = self._cache.get(key)
        now_mono = time.monotonic()
        if cached and cached[0] > now_mono and cached[1].session_expires_at > _now():
            return cached[1]
        target = self._service.validate_proxy_session(session, workspace_id, raw_token)
        self._cache[key] = (now_mono + self._ttl, target)
        return target

    def invalidate(self, workspace_id: UUID) -> None:
        self._cache = {key: value for key, value in self._cache.items() if key[0] != workspace_id}
