"""Central Repository application service.

The Repository is a logical catalog over immutable ``StoredObject`` bytes.  It
never derives product meaning or access scope: publishers must supply both.
"""

from __future__ import annotations

import base64
import json
import unicodedata
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aurinara_platform.auth import Principal
from aurinara_platform.models import (
    ObjectReference,
    ObjectStatus,
    OutboxEvent,
    Permission,
    RepositoryFileVersion,
    RepositoryJob,
    RepositoryNode,
    RepositoryNodeRevision,
    RepositoryOfficeSession,
    RepositorySourceLink,
    RepositoryVersionProfile,
    Site,
    StoredObject,
    Study,
    User,
    utc_now,
)
from aurinara_platform.problems import Problem
from aurinara_platform.schemas import (
    AuthorizationDecisionRequest,
    ObjectReferenceCreate,
    RepositoryFolderCreate,
    RepositoryNodePatch,
    RepositoryPublication,
    RepositoryStatusPatch,
    RepositoryVersionCreate,
)
from aurinara_platform.services import AuthorizationService
from aurinara_platform.storage import ObjectStorage

_HIDDEN_SOURCE_STATUSES = frozenset({"pending", "rejected", "orphaned", "purged"})


def _normalized_name(value: str) -> str:
    name = unicodedata.normalize("NFC", value).strip()
    if not name or name in {".", ".."} or any(char in name for char in "/\\"):
        raise Problem(422, "invalid_repository_name", "The Repository name is not valid")
    if any(unicodedata.category(char).startswith("C") for char in name):
        raise Problem(422, "invalid_repository_name", "The Repository name is not valid")
    return name.casefold()


def _taxonomy_label(value: str) -> str:
    special = {
        "adam": "ADaM",
        "ctms": "CTMS",
        "edc": "EDC",
        "pv": "PV",
        "sdtm": "SDTM",
        "tlf": "TLF",
    }
    normalized = value.strip().lower().replace(" ", "-")
    if normalized in special:
        return special[normalized]
    return " ".join(part.capitalize() for part in normalized.replace("_", "-").split("-") if part)


def _encode_cursor(node: RepositoryNode) -> str:
    raw = json.dumps([node.updated_at.isoformat(), str(node.id)], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, UUID]:
    try:
        padded = value + "=" * (-len(value) % 4)
        timestamp, identifier = json.loads(base64.urlsafe_b64decode(padded).decode())
        return datetime.fromisoformat(timestamp), UUID(identifier)
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise Problem(400, "invalid_cursor", "The Repository cursor is invalid") from error


class RepositoryService:
    """Manage the authorized Repository namespace and append-only versions."""

    def __init__(
        self,
        authorization: AuthorizationService,
        storage: ObjectStorage,
        presign_ttl_seconds: int,
    ) -> None:
        self._authorization = authorization
        self._storage = storage
        self._presign_ttl_seconds = presign_ttl_seconds

    def create_folder(
        self,
        session: Session,
        study_id: UUID,
        command: RepositoryFolderCreate,
        principal: Principal,
        actor: User,
    ) -> RepositoryNode:
        self._require_repository(session, principal, "platform.repository.write", study_id)
        study = session.get(Study, study_id)
        if study is None:
            raise Problem(404, "study_not_found", "The study was not found")
        parent = self._parent(session, study_id, command.parent_id)
        if command.access is None:
            if parent is None:
                product = "platform"
                read_action = "platform.repository.read"
                write_action = "platform.repository.write"
                site_id = None
            else:
                product = parent.source_product
                read_action = parent.required_read_action
                write_action = parent.required_write_action
                site_id = parent.site_id
        else:
            read_action = command.access.read_action
            write_action = command.access.write_action
            site_id = command.access.site_id
            read_permission = self._validate_action(session, read_action, None, study_id, site_id)
            self._validate_action(session, write_action, read_permission.product, study_id, site_id)
            product = read_permission.product
        if parent is not None:
            self._require_node(session, principal, parent, write=True)
            self._require_equivalent_scope(parent, product, read_action, write_action, site_id)
        normalized_name = _normalized_name(command.name)
        self._assert_name_available(session, study_id, command.parent_id, normalized_name)
        node = RepositoryNode(
            study_id=study_id,
            parent_id=command.parent_id,
            kind="folder",
            display_name=command.name.strip(),
            normalized_name=normalized_name,
            source_product=product,
            classification=command.classification,
            required_read_action=read_action,
            required_write_action=write_action,
            site_id=site_id,
            source_resource_type="repository_folder",
            source_status="available",
            created_by=actor.id,
            updated_by=actor.id,
        )
        session.add(node)
        self._flush_name(session)
        self._record(session, node, "created", actor)
        return node

    def publish_reference(
        self,
        session: Session,
        stored_object: StoredObject,
        reference: ObjectReference,
        command: ObjectReferenceCreate,
        actor: User,
    ) -> RepositoryNode | None:
        publication = command.repository
        if publication is None:
            return None
        if stored_object.status != ObjectStatus.available:
            raise Problem(409, "object_unavailable", "Only available objects can be published")
        if publication.classification != stored_object.classification:
            raise Problem(
                422,
                "repository_classification_mismatch",
                "Repository classification must match the stored object",
            )
        self._validate_publication(session, stored_object, command.product, publication)
        target_parent_id = self._publication_parent(
            session,
            stored_object.study_id,
            command.product,
            publication,
            actor,
        )
        existing_link = session.scalar(
            select(RepositorySourceLink).where(
                RepositorySourceLink.product == command.product,
                RepositorySourceLink.external_reference == command.external_reference,
            )
        )
        if existing_link is not None:
            version = session.get(RepositoryFileVersion, existing_link.version_id)
            node = session.get(RepositoryNode, existing_link.node_id)
            if (
                version is not None
                and node is not None
                and version.object_id == stored_object.id
                and node.module == publication.module
                and node.classification == publication.classification
                and node.required_read_action == publication.access.read_action
                and node.required_write_action == publication.access.write_action
                and node.site_id == publication.access.site_id
                and existing_link.source_type == publication.source_type
                and existing_link.source_id == publication.source_id
                and existing_link.source_status == publication.source_status
                and existing_link.provenance == publication.provenance
            ):
                if node.parent_id != target_parent_id:
                    node.parent_id = target_parent_id
                    node.revision += 1
                    node.updated_by = actor.id
                    node.updated_at = utc_now()
                    self._assert_name_available(
                        session,
                        node.study_id,
                        target_parent_id,
                        node.normalized_name,
                        exclude_id=node.id,
                    )
                    self._record(session, node, "hierarchy_organized", actor)
                    self._emit(session, node, "platform.repository.metadata.changed.v1")
                return node
            raise Problem(409, "repository_source_conflict", "The source is already published")

        parent = self._parent(session, stored_object.study_id, target_parent_id)
        if parent is not None and publication.parent_id is not None:
            self._require_equivalent_scope(
                parent,
                command.product,
                publication.access.read_action,
                publication.access.write_action,
                publication.access.site_id,
            )
        display_name = self._available_publication_name(
            session,
            stored_object.study_id,
            target_parent_id,
            publication.filename,
            stored_object.id,
        )
        node = RepositoryNode(
            study_id=stored_object.study_id,
            parent_id=target_parent_id,
            kind="file",
            display_name=display_name,
            normalized_name=_normalized_name(display_name),
            source_product=command.product,
            module=publication.module,
            classification=publication.classification,
            required_read_action=publication.access.read_action,
            required_write_action=publication.access.write_action,
            site_id=publication.access.site_id,
            source_resource_type=publication.source_type,
            source_resource_id=publication.source_id,
            source_status=publication.source_status,
            retention_metadata=publication.retention_metadata,
            current_version_number=1,
            created_by=actor.id,
            updated_by=actor.id,
        )
        session.add(node)
        self._flush_name(session)
        version = RepositoryFileVersion(
            node_id=node.id,
            version_number=1,
            object_id=stored_object.id,
            object_reference_id=reference.id,
            created_by=actor.id,
        )
        session.add(version)
        session.flush()
        self.queue_analysis(session, version)
        session.add(
            RepositorySourceLink(
                node_id=node.id,
                version_id=version.id,
                object_reference_id=reference.id,
                product=command.product,
                external_reference=command.external_reference,
                source_type=publication.source_type,
                source_id=publication.source_id,
                source_status=publication.source_status,
                provenance=publication.provenance,
            )
        )
        self._record(session, node, "created", actor)
        self._emit(session, node, "platform.repository.file.created.v1")
        return node

    def _publication_parent(
        self,
        session: Session,
        study_id: UUID,
        product: str,
        publication: RepositoryPublication,
        actor: User,
    ) -> UUID | None:
        if publication.parent_id is not None:
            return publication.parent_id
        if publication.solution is None or publication.purpose is None:
            return None
        study = session.get(Study, study_id)
        if study is None:
            raise Problem(404, "study_not_found", "The study was not found")
        module_key = publication.module or product
        levels = (
            (
                "study",
                str(study.id),
                f"{study.code} · {study.display_name}",
                None,
            ),
            ("module", module_key, _taxonomy_label(module_key), module_key),
            (
                "solution",
                publication.solution,
                publication.solution_label or _taxonomy_label(publication.solution),
                module_key,
            ),
            ("purpose", publication.purpose, _taxonomy_label(publication.purpose), module_key),
        )
        parent_id: UUID | None = None
        for level, key, label, module in levels:
            folder = self._ensure_structure_folder(
                session,
                study_id=study_id,
                parent_id=parent_id,
                level=level,
                key=key,
                label=label,
                module=module,
                actor=actor,
            )
            parent_id = folder.id
        return parent_id

    def _ensure_structure_folder(
        self,
        session: Session,
        *,
        study_id: UUID,
        parent_id: UUID | None,
        level: str,
        key: str,
        label: str,
        module: str | None,
        actor: User,
    ) -> RepositoryNode:
        resource_type = f"repository_structure_{level}"
        folder = session.scalar(
            select(RepositoryNode).where(
                RepositoryNode.study_id == study_id,
                RepositoryNode.parent_id == parent_id,
                RepositoryNode.kind == "folder",
                RepositoryNode.source_resource_type == resource_type,
                RepositoryNode.source_resource_id == key,
                RepositoryNode.lifecycle_status == "active",
            )
        )
        if folder is not None:
            if folder.display_name != label:
                self._assert_name_available(
                    session,
                    study_id,
                    parent_id,
                    _normalized_name(label),
                    exclude_id=folder.id,
                )
                folder.display_name = label
                folder.normalized_name = _normalized_name(label)
                folder.revision += 1
                folder.updated_by = actor.id
                folder.updated_at = utc_now()
                self._record(session, folder, "taxonomy_label_refreshed", actor)
                self._emit(session, folder, "platform.repository.metadata.changed.v1")
            return folder
        self._assert_name_available(session, study_id, parent_id, _normalized_name(label))
        folder = RepositoryNode(
            study_id=study_id,
            parent_id=parent_id,
            kind="folder",
            display_name=label,
            normalized_name=_normalized_name(label),
            source_product="platform",
            module=module,
            classification="internal",
            required_read_action="platform.repository.read",
            required_write_action="platform.repository.write",
            source_resource_type=resource_type,
            source_resource_id=key,
            source_status="available",
            created_by=actor.id,
            updated_by=actor.id,
        )
        session.add(folder)
        self._flush_name(session)
        self._record(session, folder, "created", actor)
        self._emit(session, folder, "platform.repository.folder.created.v1")
        return folder

    def publish_existing_reference(
        self,
        session: Session,
        reference_id: UUID,
        publication: RepositoryPublication,
        principal: Principal,
        actor: User,
    ) -> RepositoryNode:
        """Publish one exact active product reference during reviewed backfill."""

        reference = session.get(ObjectReference, reference_id)
        if reference is None:
            raise Problem(404, "object_reference_not_found", "The object reference was not found")
        if reference.released_at is not None:
            raise Problem(409, "object_reference_released", "The object reference was released")
        stored_object = session.get(StoredObject, reference.object_id)
        if stored_object is None:
            raise Problem(409, "stored_object_missing", "The referenced object is missing")
        action = reference.authorization_action or f"{reference.product}.object.reference"
        self._authorization.require(
            session,
            principal,
            action,
            product=reference.product,
            resource_type="object_reference",
            resource_id=str(reference.id),
            study_id=stored_object.study_id,
        )
        command = ObjectReferenceCreate(
            product=reference.product,
            authorization_action=action,
            external_reference=reference.external_reference,
            repository=publication,
        )
        return self.publish_reference(session, stored_object, reference, command, actor)

    def list_nodes(
        self,
        session: Session,
        study_id: UUID,
        principal: Principal,
        *,
        parent_id: UUID | None,
        view: str,
        query: str | None,
        product: str | None,
        module: str | None,
        classification: str | None,
        source_status: str | None,
        limit: int,
        after: str | None,
    ) -> tuple[list[RepositoryNode], str | None]:
        self._require_repository(session, principal, "platform.repository.read", study_id)
        active_reference = exists(
            select(1)
            .select_from(RepositoryFileVersion)
            .join(
                ObjectReference,
                ObjectReference.id == RepositoryFileVersion.object_reference_id,
            )
            .where(
                RepositoryFileVersion.node_id == RepositoryNode.id,
                ObjectReference.released_at.is_(None),
            )
        )
        statement = select(RepositoryNode).where(
            RepositoryNode.study_id == study_id,
            RepositoryNode.source_status.not_in(_HIDDEN_SOURCE_STATUSES),
            or_(RepositoryNode.kind == "folder", active_reference),
        )
        if view == "folder":
            statement = statement.where(RepositoryNode.parent_id == parent_id)
        elif view == "recent":
            statement = statement.where(RepositoryNode.updated_at >= utc_now() - timedelta(days=30))
        elif view == "archived":
            statement = statement.where(RepositoryNode.lifecycle_status == "archived")
        elif view in {"aurinara", "aurinara-edc", "platform"}:
            statement = statement.where(RepositoryNode.source_product == view)
        elif view != "all":
            raise Problem(422, "invalid_repository_view", "The Repository view is not valid")
        if view != "archived":
            statement = statement.where(RepositoryNode.lifecycle_status == "active")
        if query:
            statement = statement.where(
                func.lower(RepositoryNode.display_name).contains(query.strip().lower())
            )
        if product:
            statement = statement.where(RepositoryNode.source_product == product)
        if module:
            statement = statement.where(RepositoryNode.module == module)
        if classification:
            statement = statement.where(RepositoryNode.classification == classification)
        if source_status:
            statement = statement.where(RepositoryNode.source_status == source_status)
        if after:
            cursor_time, cursor_id = _decode_cursor(after)
            statement = statement.where(
                or_(
                    RepositoryNode.updated_at < cursor_time,
                    and_(RepositoryNode.updated_at == cursor_time, RepositoryNode.id < cursor_id),
                )
            )
        candidates = list(
            session.scalars(
                statement.order_by(
                    RepositoryNode.updated_at.desc(), RepositoryNode.id.desc()
                ).limit(min(limit * 8, 800))
            )
        )
        allowed = [node for node in candidates if self._can_read(session, principal, node)]
        page = allowed[:limit]
        next_cursor = _encode_cursor(page[-1]) if len(allowed) > limit and page else None
        return page, next_cursor

    def get_node(
        self, session: Session, node_id: UUID, principal: Principal, *, write: bool = False
    ) -> RepositoryNode:
        node = self._node(session, node_id)
        if node.source_status in _HIDDEN_SOURCE_STATUSES:
            raise Problem(404, "repository_node_not_found", "The Repository node was not found")
        if write and self._is_structure_node(node):
            raise Problem(
                409,
                "repository_structure_managed",
                "Study, module, solution, and purpose folders are managed by Platform",
            )
        self._require_repository(
            session,
            principal,
            "platform.repository.write" if write else "platform.repository.read",
            node.study_id,
        )
        if self._is_structure_node(node) and not self._can_read(session, principal, node):
            raise Problem(404, "repository_node_not_found", "The Repository node was not found")
        self._require_node(session, principal, node, write=write)
        return node

    def patch_node(
        self,
        session: Session,
        node_id: UUID,
        command: RepositoryNodePatch,
        expected_revision: int,
        principal: Principal,
        actor: User,
    ) -> RepositoryNode:
        node = self.get_node(session, node_id, principal, write=True)
        if node.revision != expected_revision:
            raise Problem(412, "repository_revision_mismatch", "The Repository entry has changed")
        if node.lifecycle_status != "active":
            raise Problem(409, "repository_node_archived", "Restore the entry before changing it")
        action = "metadata_changed"
        if command.name is not None:
            node.display_name = command.name.strip()
            node.normalized_name = _normalized_name(command.name)
            action = "renamed"
        if "parent_id" in command.model_fields_set and command.parent_id != node.parent_id:
            parent = self._parent(session, node.study_id, command.parent_id)
            if parent is not None:
                if parent.id == node.id or self._is_descendant(session, parent.id, node.id):
                    raise Problem(409, "repository_cycle", "A folder cannot be moved below itself")
                self._require_node(session, principal, parent, write=True)
                self._require_equivalent_scope(
                    parent,
                    node.source_product,
                    node.required_read_action,
                    node.required_write_action,
                    node.site_id,
                )
            node.parent_id = command.parent_id
            action = "moved"
        if command.source_status is not None:
            node.source_status = command.source_status
        node.revision += 1
        node.updated_by = actor.id
        node.updated_at = utc_now()
        self._assert_name_available(
            session,
            node.study_id,
            node.parent_id,
            node.normalized_name,
            exclude_id=node.id,
        )
        self._flush_name(session)
        self._record(session, node, action, actor)
        self._emit(session, node, "platform.repository.metadata.changed.v1")
        return node

    def archive(
        self,
        session: Session,
        node_id: UUID,
        expected_revision: int,
        principal: Principal,
        actor: User,
    ) -> RepositoryNode:
        node = self.get_node(session, node_id, principal, write=True)
        if node.revision != expected_revision:
            raise Problem(412, "repository_revision_mismatch", "The Repository entry has changed")
        now = utc_now()
        for item in self._subtree(session, node):
            item.lifecycle_status = "archived"
            item.archived_at = now
            item.revision += 1
            item.updated_by = actor.id
            item.updated_at = now
            self._record(session, item, "archived", actor)
            self._emit(session, item, "platform.repository.node.archived.v1")
        return node

    def restore(
        self,
        session: Session,
        node_id: UUID,
        expected_revision: int,
        principal: Principal,
        actor: User,
    ) -> RepositoryNode:
        node = self.get_node(session, node_id, principal, write=True)
        if node.revision != expected_revision:
            raise Problem(412, "repository_revision_mismatch", "The Repository entry has changed")
        for item in self._subtree(session, node):
            if self._active_name_exists(session, item):
                raise Problem(
                    409,
                    "repository_name_conflict",
                    "Restore would conflict with an active entry of the same name",
                )
        now = utc_now()
        for item in self._subtree(session, node):
            item.lifecycle_status = "active"
            item.archived_at = None
            item.revision += 1
            item.updated_by = actor.id
            item.updated_at = now
            self._record(session, item, "restored", actor)
            self._emit(session, item, "platform.repository.node.restored.v1")
        return node

    def add_version(
        self,
        session: Session,
        node_id: UUID,
        command: RepositoryVersionCreate,
        principal: Principal,
        actor: User,
    ) -> RepositoryFileVersion:
        node = self.get_node(session, node_id, principal, write=True)
        if node.kind != "file" or node.lifecycle_status != "active":
            raise Problem(409, "invalid_repository_node", "The entry cannot accept a version")
        active_office_session = session.scalar(
            select(RepositoryOfficeSession.id).where(
                RepositoryOfficeSession.node_id == node.id,
                RepositoryOfficeSession.state == "active",
                RepositoryOfficeSession.editable.is_(True),
                RepositoryOfficeSession.lease_expires_at > utc_now(),
            )
        )
        if active_office_session is not None:
            raise Problem(
                409,
                "office_edit_in_progress",
                "A collaborative Office edit is active for the current version",
            )
        if command.product != node.source_product:
            raise Problem(422, "repository_product_mismatch", "The version product does not match")
        stored_object = session.get(StoredObject, command.object_id)
        reference = session.get(ObjectReference, command.object_reference_id)
        if (
            stored_object is None
            or stored_object.study_id != node.study_id
            or stored_object.status != ObjectStatus.available
            or reference is None
            or reference.object_id != stored_object.id
            or reference.product != command.product
            or reference.released_at is not None
        ):
            raise Problem(
                422, "invalid_repository_object", "The immutable object reference is invalid"
            )
        next_number = (node.current_version_number or 0) + 1
        version = RepositoryFileVersion(
            node_id=node.id,
            version_number=next_number,
            object_id=stored_object.id,
            object_reference_id=reference.id,
            derived_from_version_id=command.derived_from_version_id,
            created_by=actor.id,
        )
        session.add(version)
        session.flush()
        self.queue_analysis(session, version)
        session.add(
            RepositorySourceLink(
                node_id=node.id,
                version_id=version.id,
                object_reference_id=reference.id,
                product=command.product,
                external_reference=command.external_reference,
                source_type=command.source_type,
                source_id=command.source_id,
                source_status=command.source_status,
                provenance=command.provenance,
            )
        )
        node.current_version_number = next_number
        node.source_status = command.source_status
        node.revision += 1
        node.updated_by = actor.id
        node.updated_at = utc_now()
        self._record(session, node, "version_added", actor)
        self._emit(session, node, "platform.repository.file.version_added.v1")
        return version

    @staticmethod
    def queue_analysis(session: Session, version: RepositoryFileVersion) -> RepositoryJob:
        profile = session.get(RepositoryVersionProfile, version.id)
        if profile is None:
            profile = RepositoryVersionProfile(version_id=version.id)
            session.add(profile)
        key = f"analyze:{version.id}"
        job = session.scalar(select(RepositoryJob).where(RepositoryJob.idempotency_key == key))
        if job is None:
            job = RepositoryJob(
                job_type="analyze",
                version_id=version.id,
                requested_by=version.created_by,
                idempotency_key=key,
            )
            session.add(job)
        return job

    def list_versions(
        self, session: Session, node_id: UUID, principal: Principal
    ) -> list[tuple[RepositoryFileVersion, StoredObject]]:
        node = self.get_node(session, node_id, principal)
        if node.kind != "file":
            raise Problem(409, "invalid_repository_node", "Folders do not have versions")
        return list(
            session.execute(
                select(RepositoryFileVersion, StoredObject)
                .join(StoredObject, StoredObject.id == RepositoryFileVersion.object_id)
                .where(RepositoryFileVersion.node_id == node.id)
                .order_by(RepositoryFileVersion.version_number.desc())
            ).all()
        )

    def resolve_version(
        self, session: Session, version_id: UUID, principal: Principal
    ) -> tuple[RepositoryNode, RepositoryFileVersion, StoredObject]:
        row = session.execute(
            select(RepositoryNode, RepositoryFileVersion, StoredObject)
            .join(RepositoryFileVersion, RepositoryFileVersion.node_id == RepositoryNode.id)
            .join(StoredObject, StoredObject.id == RepositoryFileVersion.object_id)
            .where(RepositoryFileVersion.id == version_id)
        ).one_or_none()
        if row is None:
            raise Problem(404, "repository_version_not_found", "The file version was not found")
        node, version, stored_object = row
        self._require_repository(session, principal, "platform.repository.read", node.study_id)
        self._require_node(session, principal, node, write=False)
        return node, version, stored_object

    def download(
        self, session: Session, version_id: UUID, principal: Principal
    ) -> tuple[str, datetime]:
        _, _, stored_object = self.resolve_version(session, version_id, principal)
        if stored_object.status != ObjectStatus.available:
            raise Problem(409, "object_unavailable", "The object is not available")
        return (
            self._storage.presign_download(stored_object.object_key),
            datetime.now(UTC) + timedelta(seconds=self._presign_ttl_seconds),
        )

    def synchronize_status(
        self,
        session: Session,
        node_id: UUID,
        command: RepositoryStatusPatch,
        principal: Principal,
        actor: User,
    ) -> RepositoryNode:
        node = self._node(session, node_id)
        link = session.scalar(
            select(RepositorySourceLink).where(
                RepositorySourceLink.node_id == node.id,
                RepositorySourceLink.product == node.source_product,
                RepositorySourceLink.external_reference == command.external_reference,
            )
        )
        if link is None:
            raise Problem(404, "repository_source_not_found", "The source link was not found")
        reference = session.get(ObjectReference, link.object_reference_id)
        if reference is None:
            raise Problem(
                409,
                "repository_reference_missing",
                "The source object reference is missing",
            )
        self._authorization.require(
            session,
            principal,
            reference.authorization_action or f"{node.source_product}.object.reference",
            product=node.source_product,
            resource_type="repository_source",
            resource_id=str(node.id),
            study_id=node.study_id,
            site_id=node.site_id,
        )
        if node.source_status != command.source_status:
            node.source_status = command.source_status
            node.revision += 1
            node.updated_by = actor.id
            node.updated_at = utc_now()
            self._record(session, node, "source_status_changed", actor)
            self._emit(session, node, "platform.repository.metadata.changed.v1")
        return node

    def _validate_publication(
        self,
        session: Session,
        stored_object: StoredObject,
        product: str,
        publication: RepositoryPublication,
    ) -> None:
        self._validate_action(
            session,
            publication.access.read_action,
            product,
            stored_object.study_id,
            publication.access.site_id,
        )
        self._validate_action(
            session,
            publication.access.write_action,
            product,
            stored_object.study_id,
            publication.access.site_id,
        )
        _normalized_name(publication.filename)

    @staticmethod
    def _validate_action(
        session: Session,
        action: str,
        product: str | None,
        study_id: UUID,
        site_id: UUID | None,
    ) -> Permission:
        permission = session.get(Permission, action)
        if permission is None or permission.status != "active":
            raise Problem(
                422, "repository_permission_not_found", "The declared permission is invalid"
            )
        if product is not None and permission.product != product:
            raise Problem(
                422,
                "repository_permission_product_mismatch",
                "The declared permission does not belong to the publishing product",
            )
        required_scope = "site" if site_id else "study"
        if (
            required_scope not in permission.allowed_scope_levels
            and "system" not in permission.allowed_scope_levels
        ):
            raise Problem(
                422, "repository_permission_scope_mismatch", "The permission cannot use this scope"
            )
        if session.get(Study, study_id) is None:
            raise Problem(404, "study_not_found", "The study was not found")
        if site_id is not None:
            site = session.get(Site, site_id)
            if site is None or site.study_id != study_id or site.status != "active":
                raise Problem(422, "repository_site_invalid", "The declared site is not canonical")
        return permission

    def _require_repository(
        self, session: Session, principal: Principal, action: str, study_id: UUID
    ) -> None:
        self._authorization.require(
            session,
            principal,
            action,
            product="platform",
            resource_type="repository",
            study_id=study_id,
        )

    def _require_node(
        self, session: Session, principal: Principal, node: RepositoryNode, *, write: bool
    ) -> None:
        self._authorization.require(
            session,
            principal,
            node.required_write_action if write else node.required_read_action,
            product=node.source_product,
            resource_type="repository_node",
            resource_id=str(node.id),
            study_id=node.study_id,
            site_id=node.site_id,
        )

    def _can_read(self, session: Session, principal: Principal, node: RepositoryNode) -> bool:
        if self._is_structure_node(node):
            return self._has_readable_descendant(session, principal, node)
        return self._can_read_direct(session, principal, node)

    def _can_read_direct(
        self, session: Session, principal: Principal, node: RepositoryNode
    ) -> bool:
        decision = self._authorization.decide(
            session,
            AuthorizationDecisionRequest.model_validate(
                {
                    "principal": {"issuer": principal.issuer, "subject": principal.subject},
                    "action": node.required_read_action,
                    "resource": {
                        "product": node.source_product,
                        "type": "repository_node",
                        "id": str(node.id),
                        "study_id": node.study_id,
                        "site_id": node.site_id,
                    },
                }
            ),
        )
        return decision.allowed

    def _has_readable_descendant(
        self, session: Session, principal: Principal, node: RepositoryNode
    ) -> bool:
        frontier = [node.id]
        visited: set[UUID] = set()
        while frontier:
            parent_ids = [identifier for identifier in frontier if identifier not in visited]
            if not parent_ids:
                return False
            visited.update(parent_ids)
            children = list(
                session.scalars(
                    select(RepositoryNode).where(
                        RepositoryNode.parent_id.in_(parent_ids),
                        RepositoryNode.lifecycle_status == "active",
                        RepositoryNode.source_status.not_in(_HIDDEN_SOURCE_STATUSES),
                    )
                )
            )
            frontier = [child.id for child in children if child.kind == "folder"]
            for child in children:
                if (
                    child.kind == "file"
                    and self._has_active_reference(session, child.id)
                    and self._can_read_direct(session, principal, child)
                ):
                    return True
        return False

    @staticmethod
    def _has_active_reference(session: Session, node_id: UUID) -> bool:
        return bool(
            session.scalar(
                select(
                    exists(
                        select(1)
                        .select_from(RepositoryFileVersion)
                        .join(
                            ObjectReference,
                            ObjectReference.id == RepositoryFileVersion.object_reference_id,
                        )
                        .where(
                            RepositoryFileVersion.node_id == node_id,
                            ObjectReference.released_at.is_(None),
                        )
                    )
                )
            )
        )

    @staticmethod
    def _is_structure_node(node: RepositoryNode) -> bool:
        return node.source_resource_type.startswith("repository_structure_")

    @staticmethod
    def _require_equivalent_scope(
        parent: RepositoryNode,
        product: str,
        read_action: str,
        write_action: str,
        site_id: UUID | None,
    ) -> None:
        if (
            parent.source_product != product
            or parent.required_read_action != read_action
            or parent.required_write_action != write_action
            or parent.site_id != site_id
        ):
            raise Problem(
                409,
                "repository_scope_mismatch",
                "The destination folder does not have an equivalent access scope",
            )

    @staticmethod
    def _parent(session: Session, study_id: UUID, parent_id: UUID | None) -> RepositoryNode | None:
        if parent_id is None:
            return None
        parent = session.get(RepositoryNode, parent_id)
        if (
            parent is None
            or parent.study_id != study_id
            or parent.kind != "folder"
            or parent.lifecycle_status != "active"
        ):
            raise Problem(422, "repository_parent_invalid", "The destination folder is invalid")
        return parent

    @staticmethod
    def _node(session: Session, node_id: UUID) -> RepositoryNode:
        node = session.get(RepositoryNode, node_id)
        if node is None:
            raise Problem(404, "repository_node_not_found", "The Repository entry was not found")
        return node

    @staticmethod
    def _available_publication_name(
        session: Session,
        study_id: UUID,
        parent_id: UUID | None,
        filename: str,
        object_id: UUID,
    ) -> str:
        cleaned = filename.strip()
        normalized = _normalized_name(cleaned)
        exists = session.scalar(
            select(RepositoryNode.id).where(
                RepositoryNode.study_id == study_id,
                RepositoryNode.parent_id == parent_id,
                RepositoryNode.normalized_name == normalized,
                RepositoryNode.lifecycle_status == "active",
            )
        )
        if exists is None:
            return cleaned
        dot = cleaned.rfind(".")
        suffix = str(object_id)[:8]
        return f"{cleaned[:dot]} ({suffix}){cleaned[dot:]}" if dot > 0 else f"{cleaned} ({suffix})"

    @staticmethod
    def _assert_name_available(
        session: Session,
        study_id: UUID,
        parent_id: UUID | None,
        normalized_name: str,
        *,
        exclude_id: UUID | None = None,
    ) -> None:
        statement = select(RepositoryNode.id).where(
            RepositoryNode.study_id == study_id,
            RepositoryNode.parent_id == parent_id,
            RepositoryNode.normalized_name == normalized_name,
            RepositoryNode.lifecycle_status == "active",
        )
        if exclude_id is not None:
            statement = statement.where(RepositoryNode.id != exclude_id)
        if session.scalar(statement) is not None:
            raise Problem(
                409,
                "repository_name_conflict",
                "An active entry with this name already exists in the folder",
            )

    @staticmethod
    def _flush_name(session: Session) -> None:
        try:
            session.flush()
        except IntegrityError as error:
            raise Problem(
                409,
                "repository_name_conflict",
                "An active entry with this name already exists in the folder",
            ) from error

    @staticmethod
    def _active_name_exists(session: Session, node: RepositoryNode) -> bool:
        return bool(
            session.scalar(
                select(RepositoryNode.id).where(
                    RepositoryNode.study_id == node.study_id,
                    RepositoryNode.parent_id == node.parent_id,
                    RepositoryNode.normalized_name == node.normalized_name,
                    RepositoryNode.lifecycle_status == "active",
                    RepositoryNode.id != node.id,
                )
            )
        )

    @staticmethod
    def _subtree(session: Session, root: RepositoryNode) -> list[RepositoryNode]:
        items = [root]
        queue = [root.id]
        while queue:
            children = list(
                session.scalars(select(RepositoryNode).where(RepositoryNode.parent_id.in_(queue)))
            )
            items.extend(children)
            queue = [item.id for item in children]
        return items

    @staticmethod
    def _is_descendant(session: Session, candidate_id: UUID, root_id: UUID) -> bool:
        current = session.get(RepositoryNode, candidate_id)
        while current is not None and current.parent_id is not None:
            if current.parent_id == root_id:
                return True
            current = session.get(RepositoryNode, current.parent_id)
        return False

    @staticmethod
    def _snapshot(node: RepositoryNode) -> dict[str, object]:
        return {
            "id": str(node.id),
            "study_id": str(node.study_id),
            "parent_id": str(node.parent_id) if node.parent_id else None,
            "kind": node.kind,
            "display_name": node.display_name,
            "source_product": node.source_product,
            "module": node.module,
            "classification": node.classification,
            "required_read_action": node.required_read_action,
            "required_write_action": node.required_write_action,
            "site_id": str(node.site_id) if node.site_id else None,
            "source_resource_type": node.source_resource_type,
            "source_resource_id": node.source_resource_id,
            "source_status": node.source_status,
            "lifecycle_status": node.lifecycle_status,
            "current_version_number": node.current_version_number,
            "revision": node.revision,
        }

    def _record(
        self,
        session: Session,
        node: RepositoryNode,
        action: str,
        actor: User,
        reason: str | None = None,
    ) -> None:
        session.add(
            RepositoryNodeRevision(
                node_id=node.id,
                revision=node.revision,
                action=action,
                actor_id=actor.id,
                reason=reason,
                snapshot=self._snapshot(node),
            )
        )

    def _emit(self, session: Session, node: RepositoryNode, event_type: str) -> None:
        session.add(
            OutboxEvent(
                event_type=event_type,
                aggregate_type="repository_node",
                aggregate_id=node.id,
                sequence=node.revision,
                payload={
                    "node_id": str(node.id),
                    "study_id": str(node.study_id),
                    "revision": node.revision,
                    "source_product": node.source_product,
                },
            )
        )
