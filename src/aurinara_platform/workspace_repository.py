"""Authorized filesystem bridge between workspaces and the central Repository."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from aurinara_platform.auth import Principal
from aurinara_platform.config import Settings
from aurinara_platform.models import (
    ObjectReference,
    ObjectStatus,
    RepositoryFileVersion,
    RepositoryNode,
    StoredObject,
    Study,
    User,
    Workspace,
    utc_now,
)
from aurinara_platform.problems import Problem
from aurinara_platform.repository import RepositoryService
from aurinara_platform.schemas import RepositoryVersionCreate
from aurinara_platform.storage import MalwareScanner, ObjectStorage, checksum_key

_HIDDEN_SOURCE_STATUSES = frozenset({"pending", "rejected", "orphaned", "purged"})
_UNSAFE_PATH_CHARS = re.compile(r'[<>:"/\\|?*]')


@dataclass(frozen=True, slots=True)
class WorkspaceRepositoryEntry:
    path: str
    study_id: UUID
    node_id: UUID
    version_id: UUID
    sha256: str
    size_bytes: int
    media_type: str
    writable: bool


def _path_component(value: str) -> str:
    cleaned = _UNSAFE_PATH_CHARS.sub("_", value).strip().rstrip(".")
    return cleaned or "unnamed"


class WorkspaceRepositoryService:
    """Present authorized Repository versions as a small synchronized file tree."""

    def __init__(
        self,
        settings: Settings,
        repository: RepositoryService,
        storage: ObjectStorage,
        scanner: MalwareScanner,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._storage = storage
        self._scanner = scanner

    @staticmethod
    def _product_allowed(workspace: Workspace, node: RepositoryNode) -> bool:
        return workspace.product == "platform" or node.source_product == workspace.product

    def manifest(
        self,
        session: Session,
        workspace: Workspace,
        principal: Principal,
    ) -> list[WorkspaceRepositoryEntry]:
        entries: list[WorkspaceRepositoryEntry] = []
        studies = session.scalars(
            select(Study).where(Study.status == "active").order_by(Study.code)
        ).all()
        for study in studies:
            try:
                self._repository._require_repository(
                    session, principal, "platform.repository.read", study.id
                )
            except Problem:
                continue
            nodes = list(
                session.scalars(
                    select(RepositoryNode).where(
                        RepositoryNode.study_id == study.id,
                        RepositoryNode.lifecycle_status == "active",
                        RepositoryNode.source_status.not_in(_HIDDEN_SOURCE_STATUSES),
                    )
                )
            )
            by_id = {node.id: node for node in nodes}
            for node in nodes:
                if (
                    node.kind != "file"
                    or node.current_version_number is None
                    or not self._product_allowed(workspace, node)
                ):
                    continue
                try:
                    self._repository.get_node(session, node.id, principal)
                except Problem:
                    continue
                version = session.scalar(
                    select(RepositoryFileVersion).where(
                        RepositoryFileVersion.node_id == node.id,
                        RepositoryFileVersion.version_number == node.current_version_number,
                    )
                )
                if version is None:
                    continue
                stored = session.get(StoredObject, version.object_id)
                if stored is None or stored.status != ObjectStatus.available:
                    continue
                parts = [_path_component(node.display_name)]
                parent_id = node.parent_id
                visited: set[UUID] = set()
                while parent_id is not None and parent_id not in visited:
                    visited.add(parent_id)
                    parent = by_id.get(parent_id)
                    if parent is None:
                        break
                    parts.append(_path_component(parent.display_name))
                    parent_id = parent.parent_id
                writable = True
                try:
                    self._repository.get_node(session, node.id, principal, write=True)
                except Problem:
                    writable = False
                entries.append(
                    WorkspaceRepositoryEntry(
                        path="/".join([_path_component(study.code), *reversed(parts)]),
                        study_id=study.id,
                        node_id=node.id,
                        version_id=version.id,
                        sha256=stored.sha256 or "",
                        size_bytes=stored.size_bytes or 0,
                        media_type=stored.media_type,
                        writable=writable,
                    )
                )
        paths: set[str] = set()
        unique_entries: list[WorkspaceRepositoryEntry] = []
        for entry in sorted(entries, key=lambda item: (item.path.casefold(), str(item.node_id))):
            candidate = entry.path
            normalized = candidate.casefold()
            if normalized in paths:
                path = PurePosixPath(candidate)
                candidate = str(
                    path.with_name(f"{path.stem}__{entry.node_id.hex[:8]}{path.suffix}")
                )
                normalized = candidate.casefold()
            paths.add(normalized)
            unique_entries.append(replace(entry, path=candidate))
        return unique_entries

    def download(
        self,
        session: Session,
        workspace: Workspace,
        principal: Principal,
        version_id: UUID,
    ) -> tuple[str, int, Iterator[bytes]]:
        node, _, stored = self._repository.resolve_version(session, version_id, principal)
        if not self._product_allowed(workspace, node):
            raise Problem(404, "repository_version_not_found", "The file version was not found")
        if stored.status != ObjectStatus.available:
            raise Problem(409, "object_unavailable", "The object is not available")
        return (
            stored.media_type,
            stored.size_bytes or 0,
            self._storage.iter_bytes(stored.object_key),
        )

    def replace(
        self,
        session: Session,
        workspace: Workspace,
        principal: Principal,
        actor: User,
        node_id: UUID,
        base_version_id: UUID,
        chunks: Iterable[bytes],
        *,
        sha256: str,
        size_bytes: int,
        media_type: str,
    ) -> WorkspaceRepositoryEntry:
        if size_bytes <= 0 or size_bytes > self._settings.object_max_bytes:
            raise Problem(413, "object_too_large", "The object exceeds the configured size limit")
        node = session.scalar(
            select(RepositoryNode).where(RepositoryNode.id == node_id).with_for_update()
        )
        if node is None or node.kind != "file" or not self._product_allowed(workspace, node):
            raise Problem(404, "repository_node_not_found", "The Repository node was not found")
        node = self._repository.get_node(session, node.id, principal, write=True)
        current = session.scalar(
            select(RepositoryFileVersion).where(
                RepositoryFileVersion.node_id == node.id,
                RepositoryFileVersion.version_number == node.current_version_number,
            )
        )
        if current is None or current.id != base_version_id:
            raise Problem(
                409,
                "workspace_repository_conflict",
                "The Repository file changed after it was opened in Workspace",
            )
        current_object = session.get(StoredObject, current.object_id)
        if current_object is not None and current_object.sha256 == sha256:
            return self._entry_for_version(session, node, current, current_object, True)

        # StoredObject de-duplicates by study/checksum. Serialize that lookup and
        # insert across different Repository files in the same study.
        session.execute(select(Study.id).where(Study.id == node.study_id).with_for_update())
        stored = session.scalar(
            select(StoredObject).where(
                StoredObject.study_id == node.study_id,
                StoredObject.sha256 == sha256,
                StoredObject.status == ObjectStatus.available,
            )
        )
        if stored is None:
            key = checksum_key(self._settings.environment, str(node.study_id), sha256)
            stored = StoredObject(
                study_id=node.study_id,
                owner_product=node.source_product,
                upload_authorization_action=node.required_write_action,
                classification=node.classification,
                sha256=sha256,
                size_bytes=size_bytes,
                media_type=media_type,
                object_key=key,
                status=ObjectStatus.available,
                created_by=actor.id,
                finalized_at=utc_now(),
            )
            session.add(stored)
            session.flush()
            try:
                self._storage.write_stream(
                    key, chunks, media_type, sha256, size_bytes=size_bytes
                )
            except ValueError as error:
                raise Problem(
                    422,
                    "object_verification_failed",
                    "The uploaded object failed verification",
                ) from error
            if not self._scanner.scan(str(stored.id)):
                self._storage.delete(key)
                raise Problem(422, "malware_detected", "The uploaded object was rejected")

        external = f"workspace:{workspace.id}:{uuid4()}"
        reference = ObjectReference(
            object_id=stored.id,
            product=node.source_product,
            authorization_action=node.required_write_action,
            external_reference=external,
            idempotency_key=external,
            created_by=actor.id,
        )
        session.add(reference)
        session.flush()
        version = self._repository.add_version(
            session,
            node.id,
            RepositoryVersionCreate(
                object_id=stored.id,
                object_reference_id=reference.id,
                product=node.source_product,
                external_reference=external,
                source_type="workspace",
                source_id=str(workspace.id),
                source_status="available",
                provenance={
                    "workspace_id": str(workspace.id),
                    "base_version_id": str(base_version_id),
                },
                derived_from_version_id=base_version_id,
            ),
            principal,
            actor,
        )
        return self._entry_for_version(session, node, version, stored, True)

    @staticmethod
    def _entry_for_version(
        session: Session,
        node: RepositoryNode,
        version: RepositoryFileVersion,
        stored: StoredObject,
        writable: bool,
    ) -> WorkspaceRepositoryEntry:
        study = session.get(Study, node.study_id)
        study_path = _path_component(study.code if study else str(node.study_id))
        return WorkspaceRepositoryEntry(
            path=f"{study_path}/{_path_component(node.display_name)}",
            study_id=node.study_id,
            node_id=node.id,
            version_id=version.id,
            sha256=stored.sha256 or "",
            size_bytes=stored.size_bytes or 0,
            media_type=stored.media_type,
            writable=writable,
        )
