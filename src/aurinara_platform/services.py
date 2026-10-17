"""Transactional application services for the shared control plane."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from aurinara_platform.auth import Principal
from aurinara_platform.config import Settings
from aurinara_platform.models import (
    ObjectReference,
    ObjectStatus,
    OutboxEvent,
    Permission,
    PolicyState,
    Role,
    RoleAssignment,
    RolePermission,
    StoredObject,
    Study,
    StudyStatus,
    User,
    UserStatus,
    utc_now,
)
from aurinara_platform.problems import Problem
from aurinara_platform.schemas import (
    AuthorizationDecisionRequest,
    AuthorizationDecisionResponse,
    MembershipReplace,
    ObjectReferenceCreate,
    StudyCreate,
    StudyPatch,
    UploadFinalize,
    UploadPart,
    UploadSessionCreate,
    UploadSessionResponse,
    UserCreate,
    UserPatch,
)
from aurinara_platform.storage import MalwareScanner, ObjectStorage, checksum_key


def resolve_user(session: Session, principal: Principal, settings: Settings | None = None) -> User:
    user = session.scalar(
        select(User).where(User.issuer == principal.issuer, User.subject == principal.subject)
    )
    if (
        user is None
        and settings is not None
        and settings.environment == "development"
        and settings.bootstrap_first_user_as_admin
        and session.scalar(select(func.count()).select_from(User)) == 0
    ):
        user = _bootstrap_first_user(session, principal)
    if user is None:
        raise Problem(403, "identity_not_registered", "The identity is not registered")
    if user.status != UserStatus.active:
        raise Problem(403, "account_inactive", "The account is inactive")
    return user


def _bootstrap_first_user(session: Session, principal: Principal) -> User:
    """Provision the first local administrator; production forbids this mode."""

    # The console loads multiple resources in parallel. Lock the singleton policy
    # row so only one request can perform the one-time bootstrap transaction.
    _policy_state(session, lock=True)
    existing_user = session.scalar(
        select(User).where(User.issuer == principal.issuer, User.subject == principal.subject)
    )
    if existing_user is not None:
        return existing_user
    if session.scalar(select(func.count()).select_from(User)) != 0:
        raise Problem(403, "identity_not_registered", "The identity is not registered")

    user = User(
        issuer=principal.issuer,
        subject=principal.subject,
        email=principal.email,
        display_name=principal.display_name or principal.email or "Platform Administrator",
    )
    role = Role(
        product="platform",
        name="Platform Administrator",
        description="Development bootstrap administrator",
    )
    session.add_all([user, role])
    session.flush()
    codes = (
        "platform.user.manage",
        "platform.study.create",
        "platform.study.read",
        "platform.study.manage",
        "platform.membership.read",
        "platform.membership.manage",
        "platform.authorization.decide",
    )
    for code in codes:
        permission = Permission(
            code=code,
            product="platform",
            description="Development bootstrap permission",
        )
        session.add(permission)
        session.add(RolePermission(role=role, permission=permission))
    session.add(
        RoleAssignment(
            user_id=user.id,
            role_id=role.id,
            assigned_by=user.id,
        )
    )
    session.flush()
    return user


def _policy_state(session: Session, *, lock: bool = False) -> PolicyState:
    statement = select(PolicyState).where(PolicyState.id == 1)
    if lock:
        statement = statement.with_for_update()
    state = session.scalar(statement)
    if state is None:
        state = PolicyState(id=1, version=1)
        session.add(state)
        session.flush()
    return state


def _bump_policy(session: Session) -> int:
    state = _policy_state(session, lock=True)
    state.version += 1
    state.updated_at = utc_now()
    session.flush()
    return state.version


def _emit(
    session: Session,
    event_type: str,
    aggregate_type: str,
    aggregate_id: UUID,
    sequence: int,
    payload: dict[str, object],
) -> None:
    session.add(
        OutboxEvent(
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            sequence=sequence,
            payload=payload,
        )
    )


class UserService:
    def __init__(self, authorization: "AuthorizationService") -> None:
        self._authorization = authorization

    def create(self, session: Session, command: UserCreate, principal: Principal) -> User:
        self._authorization.require(
            session,
            principal,
            "platform.user.manage",
            product="platform",
            resource_type="user",
        )
        user = User(
            id=command.id or uuid4(),
            issuer=command.issuer,
            subject=command.subject,
            email=command.email,
            display_name=command.display_name,
        )
        session.add(user)
        try:
            session.flush()
        except IntegrityError as error:
            raise Problem(
                409, "user_conflict", "The user identity is already registered"
            ) from error
        self._emit_user(session, user, "platform.user.created.v1")
        return user

    def patch(
        self,
        session: Session,
        user_id: UUID,
        command: UserPatch,
        principal: Principal,
        expected_revision: int,
    ) -> User:
        self._authorization.require(
            session,
            principal,
            "platform.user.manage",
            product="platform",
            resource_type="user",
            resource_id=str(user_id),
        )
        user = session.get(User, user_id)
        if user is None:
            raise Problem(404, "user_not_found", "The user was not found")
        if user.revision != expected_revision:
            raise Problem(412, "revision_mismatch", "The user has changed")
        if command.email is not None:
            user.email = command.email
        if command.display_name is not None:
            user.display_name = command.display_name
        if command.status is not None:
            user.status = command.status
        user.revision += 1
        _bump_policy(session)
        try:
            session.flush()
        except IntegrityError as error:
            raise Problem(
                409, "user_conflict", "The user identity is already registered"
            ) from error
        self._emit_user(session, user, "platform.user.updated.v1")
        return user

    @staticmethod
    def _emit_user(session: Session, user: User, event_type: str) -> None:
        _emit(
            session,
            event_type,
            "user",
            user.id,
            user.revision,
            {
                "user_id": str(user.id),
                "issuer": user.issuer,
                "subject": user.subject,
                "email": user.email or "",
                "display_name": user.display_name or "",
                "status": user.status,
            },
        )


class AuthorizationService:
    def __init__(self, settings: Settings) -> None:
        self._ttl = settings.authorization_decision_ttl_seconds

    def decide(
        self, session: Session, request: AuthorizationDecisionRequest
    ) -> AuthorizationDecisionResponse:
        now = datetime.now(UTC)
        version = _policy_state(session).version
        user = session.scalar(
            select(User).where(
                User.issuer == request.principal.issuer,
                User.subject == request.principal.subject,
            )
        )
        if user is None:
            return self._response(False, version, now, "identity_not_registered")
        if user.status != UserStatus.active:
            return self._response(False, version, now, "account_inactive")

        resource = request.resource
        if resource.study_id is not None:
            study = session.get(Study, resource.study_id)
            if study is None:
                return self._response(False, version, now, "study_not_registered")
            if study.status != StudyStatus.active:
                return self._response(False, version, now, "study_archived")
        statement = (
            select(RoleAssignment)
            .join(Role, Role.id == RoleAssignment.role_id)
            .join(RolePermission, RolePermission.role_id == Role.id)
            .join(Permission, Permission.code == RolePermission.permission_code)
            .where(
                RoleAssignment.user_id == user.id,
                Permission.code == request.action,
            )
        )
        assignments = session.scalars(statement).all()
        allowed = any(
            self._assignment_covers(assignment, resource.study_id, resource.site_id)
            for assignment in assignments
        )
        return self._response(
            allowed,
            version,
            now,
            "permission_granted" if allowed else "permission_not_granted",
        )

    @staticmethod
    def _assignment_covers(
        assignment: RoleAssignment, study_id: UUID | None, site_id: UUID | None
    ) -> bool:
        if assignment.study_id is None and assignment.site_id is None:
            return True
        if study_id is None or assignment.study_id != study_id:
            return False
        if assignment.site_id is None:
            return True
        return site_id is not None and assignment.site_id == site_id

    def _response(
        self, allowed: bool, policy_version: int, now: datetime, reason: str
    ) -> AuthorizationDecisionResponse:
        return AuthorizationDecisionResponse(
            allowed=allowed,
            decision_id=uuid4(),
            policy_version=policy_version,
            expires_at=now + timedelta(seconds=self._ttl),
            reason_code=reason,
        )

    def require(
        self,
        session: Session,
        principal: Principal,
        action: str,
        *,
        product: str,
        resource_type: str,
        resource_id: str | None = None,
        study_id: UUID | None = None,
        site_id: UUID | None = None,
    ) -> AuthorizationDecisionResponse:
        request = AuthorizationDecisionRequest.model_validate(
            {
                "principal": {"issuer": principal.issuer, "subject": principal.subject},
                "action": action,
                "resource": {
                    "product": product,
                    "type": resource_type,
                    "id": resource_id,
                    "study_id": study_id,
                    "site_id": site_id,
                },
            }
        )
        decision = self.decide(session, request)
        if not decision.allowed:
            raise Problem(403, "authorization_denied", "The requested action is not allowed")
        return decision


class StudyService:
    def __init__(self, authorization: AuthorizationService) -> None:
        self._authorization = authorization

    def create(
        self, session: Session, command: StudyCreate, principal: Principal, actor: User
    ) -> Study:
        self._authorization.require(
            session,
            principal,
            "platform.study.create",
            product="platform",
            resource_type="study",
        )
        study = Study(
            id=command.id or uuid4(),
            code=command.code,
            display_name=command.display_name,
            created_by=actor.id,
        )
        session.add(study)
        try:
            session.flush()
        except IntegrityError as error:
            raise Problem(
                409, "study_conflict", "The study code or ID is already registered"
            ) from error
        _emit(
            session,
            "platform.study.created.v1",
            "study",
            study.id,
            study.revision,
            {
                "study_id": str(study.id),
                "code": study.code,
                "display_name": study.display_name,
                "status": study.status,
                "created_by": str(study.created_by),
            },
        )
        return study

    def visible(self, session: Session, user: User) -> list[Study]:
        statement = (
            select(Study)
            .join(RoleAssignment, (RoleAssignment.study_id == Study.id))
            .where(RoleAssignment.user_id == user.id)
            .distinct()
            .order_by(Study.code)
        )
        system_assignment = session.scalar(
            select(RoleAssignment.id).where(
                RoleAssignment.user_id == user.id, RoleAssignment.study_id.is_(None)
            )
        )
        if system_assignment is not None:
            return list(session.scalars(select(Study).order_by(Study.code)))
        return list(session.scalars(statement))

    def get(self, session: Session, study_id: UUID, principal: Principal) -> Study:
        self._authorization.require(
            session,
            principal,
            "platform.study.read",
            product="platform",
            resource_type="study",
            resource_id=str(study_id),
            study_id=study_id,
        )
        study = session.get(Study, study_id)
        if study is None:
            raise Problem(404, "study_not_found", "The study was not found")
        return study

    def patch(
        self,
        session: Session,
        study_id: UUID,
        command: StudyPatch,
        principal: Principal,
        expected_revision: int,
    ) -> Study:
        study = self.get(session, study_id, principal)
        self._authorization.require(
            session,
            principal,
            "platform.study.manage",
            product="platform",
            resource_type="study",
            resource_id=str(study_id),
            study_id=study_id,
        )
        if study.revision != expected_revision:
            raise Problem(412, "revision_mismatch", "The study has changed")
        if command.display_name is not None:
            study.display_name = command.display_name
        if command.status is not None:
            study.status = command.status
        study.revision += 1
        session.flush()
        _emit(
            session,
            "platform.study.updated.v1",
            "study",
            study.id,
            study.revision,
            {
                "study_id": str(study.id),
                "code": study.code,
                "display_name": study.display_name,
                "status": study.status,
                "created_by": str(study.created_by),
            },
        )
        return study


class MembershipService:
    def __init__(self, authorization: AuthorizationService) -> None:
        self._authorization = authorization

    def replace(
        self,
        session: Session,
        study_id: UUID,
        user_id: UUID,
        command: MembershipReplace,
        principal: Principal,
        actor: User,
    ) -> list[RoleAssignment]:
        self._authorization.require(
            session,
            principal,
            "platform.membership.manage",
            product="platform",
            resource_type="membership",
            resource_id=str(user_id),
            study_id=study_id,
            site_id=command.site_id,
        )
        if session.get(Study, study_id) is None or session.get(User, user_id) is None:
            raise Problem(404, "membership_target_not_found", "The study or user was not found")
        roles = list(session.scalars(select(Role).where(Role.id.in_(command.role_ids))))
        if len(roles) != len(set(command.role_ids)):
            raise Problem(422, "role_not_found", "One or more roles do not exist")
        session.execute(
            delete(RoleAssignment).where(
                RoleAssignment.user_id == user_id,
                RoleAssignment.study_id == study_id,
                RoleAssignment.site_id == command.site_id,
            )
        )
        assignments = [
            RoleAssignment(
                user_id=user_id,
                role_id=role.id,
                study_id=study_id,
                site_id=command.site_id,
                assigned_by=actor.id,
            )
            for role in roles
        ]
        session.add_all(assignments)
        version = _bump_policy(session)
        session.flush()
        _emit(
            session,
            "platform.membership.changed.v1",
            "study",
            study_id,
            version,
            {
                "study_id": str(study_id),
                "user_id": str(user_id),
                "site_id": str(command.site_id) if command.site_id else None,
                "role_ids": [str(item) for item in command.role_ids],
                "assigned_by": str(actor.id),
                "policy_version": version,
            },
        )
        return assignments

    def remove(
        self,
        session: Session,
        study_id: UUID,
        user_id: UUID,
        principal: Principal,
        site_id: UUID | None,
    ) -> None:
        self._authorization.require(
            session,
            principal,
            "platform.membership.manage",
            product="platform",
            resource_type="membership",
            resource_id=str(user_id),
            study_id=study_id,
            site_id=site_id,
        )
        result = session.execute(
            delete(RoleAssignment).where(
                RoleAssignment.user_id == user_id,
                RoleAssignment.study_id == study_id,
                RoleAssignment.site_id == site_id,
            )
        )
        if not result.rowcount:
            raise Problem(404, "membership_not_found", "The membership was not found")
        version = _bump_policy(session)
        _emit(
            session,
            "platform.membership.changed.v1",
            "study",
            study_id,
            version,
            {
                "study_id": str(study_id),
                "user_id": str(user_id),
                "site_id": str(site_id) if site_id else None,
                "role_ids": [],
                "policy_version": version,
            },
        )

    @staticmethod
    def list(session: Session, study_id: UUID) -> list[RoleAssignment]:
        return list(
            session.scalars(
                select(RoleAssignment)
                .options(selectinload(RoleAssignment.role))
                .where(RoleAssignment.study_id == study_id)
                .order_by(RoleAssignment.assigned_at, RoleAssignment.id)
            )
        )


class ObjectService:
    def __init__(
        self,
        settings: Settings,
        authorization: AuthorizationService,
        storage: ObjectStorage,
        scanner: MalwareScanner,
    ) -> None:
        self._settings = settings
        self._authorization = authorization
        self._storage = storage
        self._scanner = scanner

    def create_upload(
        self,
        session: Session,
        command: UploadSessionCreate,
        principal: Principal,
        actor: User,
    ) -> UploadSessionResponse:
        self._authorization.require(
            session,
            principal,
            f"{command.owner_product}.object.upload",
            product=command.owner_product,
            resource_type="object",
            study_id=command.study_id,
        )
        if command.expected_size_bytes > self._settings.object_max_bytes:
            raise Problem(413, "object_too_large", "The object exceeds the configured size limit")
        if command.part_count > self._settings.object_max_parts:
            raise Problem(422, "too_many_parts", "The upload has too many parts")
        if session.get(Study, command.study_id) is None:
            raise Problem(404, "study_not_found", "The study was not found")
        existing = session.scalar(
            select(StoredObject).where(
                StoredObject.study_id == command.study_id,
                StoredObject.sha256 == command.expected_sha256,
                StoredObject.status == ObjectStatus.available,
            )
        )
        if existing is not None:
            raise Problem(
                409,
                "object_already_exists",
                "The study already contains an object with this checksum",
                {"object_id": str(existing.id)},
            )
        key = checksum_key(
            self._settings.environment, str(command.study_id), command.expected_sha256
        )
        upload_id = self._storage.create_multipart(
            key,
            command.media_type,
            command.expected_sha256,
            command.retention_until,
        )
        expires_at = datetime.now(UTC) + timedelta(
            seconds=self._settings.object_presign_ttl_seconds
        )
        record = StoredObject(
            study_id=command.study_id,
            owner_product=command.owner_product,
            classification=command.classification,
            sha256=command.expected_sha256,
            size_bytes=command.expected_size_bytes,
            media_type=command.media_type,
            object_key=key,
            multipart_upload_id=upload_id,
            expected_part_count=command.part_count,
            upload_expires_at=expires_at,
            created_by=actor.id,
        )
        session.add(record)
        session.flush()
        return UploadSessionResponse(
            object_id=record.id,
            upload_id=upload_id,
            expires_at=expires_at,
            parts=[
                UploadPart(
                    part_number=part,
                    url=self._storage.presign_upload_part(key, upload_id, part),
                )
                for part in range(1, command.part_count + 1)
            ],
        )

    def finalize(
        self,
        session: Session,
        object_id: UUID,
        command: UploadFinalize,
        principal: Principal,
    ) -> StoredObject:
        record = self._get(session, object_id)
        self._authorization.require(
            session,
            principal,
            f"{record.owner_product}.object.upload",
            product=record.owner_product,
            resource_type="object",
            resource_id=str(record.id),
            study_id=record.study_id,
        )
        if record.status != ObjectStatus.pending or not record.multipart_upload_id:
            raise Problem(409, "invalid_object_state", "The upload cannot be finalized")
        upload_expires_at = record.upload_expires_at
        if upload_expires_at is not None and upload_expires_at.tzinfo is None:
            upload_expires_at = upload_expires_at.replace(tzinfo=UTC)
        if upload_expires_at is None or utc_now() >= upload_expires_at:
            self._storage.abort_multipart(record.object_key, record.multipart_upload_id)
            self._persist_rejection(session, record)
            raise Problem(410, "upload_expired", "The upload session has expired")
        part_numbers = [part.part_number for part in command.parts]
        expected_parts = list(range(1, (record.expected_part_count or 0) + 1))
        if part_numbers != expected_parts:
            raise Problem(
                422,
                "incomplete_upload",
                "Every upload part must be supplied exactly once and in order",
            )
        self._storage.complete_multipart(
            record.object_key,
            record.multipart_upload_id,
            [(part.part_number, part.etag) for part in command.parts],
        )
        facts = self._storage.facts(record.object_key)
        if (
            facts.size_bytes != record.size_bytes
            or facts.sha256 != record.sha256
            or facts.media_type != record.media_type
        ):
            self._storage.delete(record.object_key)
            self._persist_rejection(session, record)
            raise Problem(
                422, "object_verification_failed", "The uploaded object failed verification"
            )
        if not self._scanner.scan(str(record.id)):
            self._storage.delete(record.object_key)
            self._persist_rejection(session, record)
            raise Problem(422, "malware_detected", "The uploaded object was rejected")
        record.status = ObjectStatus.available
        record.multipart_upload_id = None
        record.finalized_at = utc_now()
        session.flush()
        _emit(
            session,
            "platform.object.available.v1",
            "object",
            record.id,
            1,
            {
                "object_id": str(record.id),
                "study_id": str(record.study_id),
                "owner_product": record.owner_product,
                "sha256": record.sha256 or "",
                "size_bytes": record.size_bytes or 0,
            },
        )
        return record

    @staticmethod
    def _persist_rejection(session: Session, record: StoredObject) -> None:
        """Persist terminal rejection before the HTTP error rolls back its request scope."""

        record.status = ObjectStatus.rejected
        record.multipart_upload_id = None
        session.commit()

    def download(self, session: Session, object_id: UUID, principal: Principal) -> str:
        record = self._get(session, object_id)
        self._authorization.require(
            session,
            principal,
            f"{record.owner_product}.object.download",
            product=record.owner_product,
            resource_type="object",
            resource_id=str(record.id),
            study_id=record.study_id,
        )
        if record.status != ObjectStatus.available:
            raise Problem(409, "object_unavailable", "The object is not available")
        return self._storage.presign_download(record.object_key)

    def metadata(self, session: Session, object_id: UUID, principal: Principal) -> StoredObject:
        record = self._get(session, object_id)
        self._authorization.require(
            session,
            principal,
            f"{record.owner_product}.object.download",
            product=record.owner_product,
            resource_type="object",
            resource_id=str(record.id),
            study_id=record.study_id,
        )
        return record

    def add_reference(
        self,
        session: Session,
        object_id: UUID,
        command: ObjectReferenceCreate,
        principal: Principal,
        actor: User,
    ) -> ObjectReference:
        record = self._get(session, object_id)
        self._authorization.require(
            session,
            principal,
            f"{command.product}.object.reference",
            product=command.product,
            resource_type="object",
            resource_id=str(record.id),
            study_id=record.study_id,
        )
        existing = session.scalar(
            select(ObjectReference).where(
                ObjectReference.product == command.product,
                ObjectReference.external_reference == command.external_reference,
            )
        )
        if existing is not None:
            if existing.object_id == record.id and existing.released_at is None:
                return existing
            raise Problem(
                409,
                "reference_conflict",
                "The product reference already exists",
                {"reference_id": str(existing.id), "object_id": str(existing.object_id)},
            )
        reference = ObjectReference(
            object_id=record.id,
            product=command.product,
            external_reference=command.external_reference,
            created_by=actor.id,
        )
        session.add(reference)
        try:
            session.flush()
        except IntegrityError as error:
            raise Problem(
                409, "reference_conflict", "The product reference already exists"
            ) from error
        return reference

    def release_reference(
        self,
        session: Session,
        reference_id: UUID,
        principal: Principal,
        actor: User,
    ) -> ObjectReference:
        del actor
        reference = session.get(ObjectReference, reference_id)
        if reference is None:
            raise Problem(404, "reference_not_found", "The object reference was not found")
        record = self._get(session, reference.object_id)
        self._authorization.require(
            session,
            principal,
            f"{reference.product}.object.reference",
            product=reference.product,
            resource_type="object",
            resource_id=str(record.id),
            study_id=record.study_id,
        )
        if reference.released_at is None:
            reference.released_at = utc_now()
            session.flush()
        return reference

    def purge_if_unreferenced(
        self, session: Session, object_id: UUID, *, now: datetime | None = None
    ) -> bool:
        """Delete bytes only after retention and all active references have cleared."""

        record = self._get(session, object_id)
        current_time = now or utc_now()
        retention_until = record.retention_until
        if retention_until is not None and retention_until.tzinfo is None:
            retention_until = retention_until.replace(tzinfo=UTC)
        if retention_until is not None and current_time < retention_until:
            return False
        references = session.scalar(
            select(func.count(ObjectReference.id)).where(
                ObjectReference.object_id == object_id,
                ObjectReference.released_at.is_(None),
            )
        )
        if references:
            return False
        if record.status != ObjectStatus.deleted:
            self._storage.delete(record.object_key)
            record.status = ObjectStatus.deleted
            session.flush()
        return True

    @staticmethod
    def _get(session: Session, object_id: UUID) -> StoredObject:
        record = session.get(StoredObject, object_id)
        if record is None:
            raise Problem(404, "object_not_found", "The object was not found")
        return record
