"""Transactional application services for the shared control plane."""

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from aurinara_platform.auth import Principal
from aurinara_platform.config import Settings
from aurinara_platform.default_access import (
    PLATFORM_STUDY_OWNER_ROLE_ID,
    SYSTEM_ADMINISTRATOR_ROLE_ID,
    ensure_platform_access_defaults,
    ensure_product_role_defaults,
    study_owner_roles,
)
from aurinara_platform.models import (
    ObjectReference,
    ObjectStatus,
    OutboxEvent,
    Permission,
    PolicyState,
    RepositoryFileVersion,
    Role,
    RoleAssignment,
    RoleChange,
    RolePermission,
    Site,
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
    PlatformRoleCreate,
    PlatformRolePatch,
    ProductPermissionSync,
    SiteSync,
    StudyCreate,
    StudyPatch,
    SystemAssignmentReplace,
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
    session.add(user)
    ensure_platform_access_defaults(session)
    session.flush()
    session.add(
        RoleAssignment(
            user_id=user.id,
            role_id=SYSTEM_ADMINISTRATOR_ROLE_ID,
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


def _emit_platform_role_change(session: Session, role: Role, version: int) -> None:
    """Publish a complete role projection before an assignment references it."""

    _emit(
        session,
        "platform.role.changed.v1",
        "role",
        role.id,
        version,
        {
            "role_id": str(role.id),
            "key": role.key,
            "source_role_id": str(role.source_role_id) if role.source_role_id else None,
            "product": role.product,
            "name": role.name,
            "description": role.description,
            "scope_level": role.scope_level,
            "assignable": role.assignable,
            "built_in": role.built_in,
            "permission_policy": role.permission_policy,
            "status": role.status,
            "revision": role.revision,
            "permission_codes": role.permission_codes,
            "policy_version": version,
        },
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

    def list(self, session: Session, principal: Principal) -> list[User]:
        self._authorization.require(
            session, principal, "platform.user.manage", product="platform", resource_type="user"
        )
        return list(session.scalars(select(User).order_by(User.display_name, User.email)))

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
        permission = session.get(Permission, request.action)
        if permission is None or permission.status != "active":
            return self._response(False, version, now, "permission_not_registered")
        if permission.product != resource.product:
            return self._response(False, version, now, "permission_product_mismatch")
        if resource.study_id is not None:
            study = session.get(Study, resource.study_id)
            if study is None:
                return self._response(False, version, now, "study_not_registered")
            if study.status != StudyStatus.active:
                return self._response(False, version, now, "study_archived")
        if resource.site_id is not None:
            site = session.get(Site, resource.site_id)
            if site is None or site.status != "active" or site.study_id != resource.study_id:
                return self._response(False, version, now, "site_not_active_in_study")
        statement = (
            select(RoleAssignment)
            .join(Role)
            .options(selectinload(RoleAssignment.role).selectinload(Role.permissions))
            .where(RoleAssignment.user_id == user.id, Role.status == "active")
        )
        assignments = session.scalars(statement).all()
        allowed = any(
            self._assignment_covers(assignment, resource.study_id, resource.site_id)
            and self._role_grants(assignment.role, permission, request.action, resource.study_id)
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

    @staticmethod
    def _role_grants(
        role: Role,
        permission: Permission,
        action: str,
        study_id: UUID | None,
    ) -> bool:
        if role.permission_policy == "all":
            return role.product == "platform" or (
                permission.product == role.product and action in role.permission_codes
            )
        if role.permission_policy == "all_scoped":
            return study_id is not None and bool(
                {"study", "site"}.intersection(permission.allowed_scope_levels)
            )
        if role.permission_policy == "read_scoped":
            return (
                study_id is not None
                and action.endswith(".read")
                and bool({"study", "site"}.intersection(permission.allowed_scope_levels))
            )
        return (
            role.scope_level in permission.allowed_scope_levels and action in role.permission_codes
        )

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
        try:
            owner_roles = study_owner_roles(session)
        except RuntimeError as error:
            raise Problem(
                503,
                "study_access_not_ready",
                "Study owner roles have not finished synchronizing",
            ) from error
        study = Study(
            id=command.id or uuid4(),
            code=command.code,
            display_name=command.display_name,
            sponsor=command.sponsor,
            created_by=actor.id,
        )
        session.add(study)
        try:
            session.flush()
        except IntegrityError as error:
            raise Problem(
                409, "study_conflict", "The study code or ID is already registered"
            ) from error
        for roles in owner_roles.values():
            session.add_all(
                RoleAssignment(
                    user_id=actor.id,
                    role_id=role.id,
                    study_id=study.id,
                    assigned_by=actor.id,
                )
                for role in roles
            )
        policy_version = _bump_policy(session)
        session.flush()
        for role in owner_roles["platform"]:
            _emit_platform_role_change(session, role, policy_version)
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
                "sponsor": study.sponsor,
                "status": study.status,
                "created_by": str(study.created_by),
            },
        )
        _emit(
            session,
            "platform.membership.changed.v1",
            "study",
            study.id,
            policy_version,
            {
                "study_id": str(study.id),
                "user_id": str(actor.id),
                "product": "platform",
                "site_id": None,
                "role_ids": [str(role.id) for role in owner_roles["platform"]],
                "assigned_by": str(actor.id),
                "policy_version": policy_version,
            },
        )
        return study

    def visible(self, session: Session, user: User) -> list[Study]:
        assignments = list(
            session.scalars(
                select(RoleAssignment)
                .join(Role)
                .options(selectinload(RoleAssignment.role).selectinload(Role.permissions))
                .where(RoleAssignment.user_id == user.id, Role.status == "active")
            )
        )
        if any(
            assignment.study_id is None
            and assignment.site_id is None
            and (
                assignment.role.permission_policy == "all"
                or "platform.study.read" in assignment.role.permission_codes
            )
            for assignment in assignments
        ):
            return list(session.scalars(select(Study).order_by(Study.code)))
        study_ids = {
            assignment.study_id
            for assignment in assignments
            if assignment.study_id is not None
            and assignment.site_id is None
            and (
                assignment.role.permission_policy in {"all_scoped", "read_scoped"}
                or "platform.study.read" in assignment.role.permission_codes
            )
        }
        if not study_ids:
            return []
        return list(
            session.scalars(
                select(Study)
                .where(
                    Study.id.in_(study_ids),
                    Study.status == StudyStatus.active,
                )
                .order_by(Study.code)
            )
        )

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
        if command.sponsor is not None:
            study.sponsor = command.sponsor
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
                "sponsor": study.sponsor,
                "status": study.status,
                "created_by": str(study.created_by),
            },
        )
        return study


class SiteService:
    def __init__(self, authorization: AuthorizationService) -> None:
        self._authorization = authorization

    def sync(
        self,
        session: Session,
        study_id: UUID,
        site_id: UUID,
        command: SiteSync,
        principal: Principal,
    ) -> Site:
        self._authorization.require(
            session,
            principal,
            "edc.site.sync",
            product="aurinara-edc",
            resource_type="site_catalog",
            resource_id=str(site_id),
            study_id=study_id,
        )
        if session.get(Study, study_id) is None:
            raise Problem(404, "study_not_found", "The study was not found")
        site = session.get(Site, site_id)
        if site is not None and site.study_id != study_id:
            raise Problem(409, "site_conflict", "The Site ID belongs to another study")
        if site is None:
            site = Site(id=site_id, study_id=study_id, name=command.name)
            session.add(site)
        site.name = command.name
        site.country = command.country
        site.state = command.state
        site.city = command.city
        site.status = command.status
        session.flush()
        _emit(
            session,
            "platform.site.synced.v1",
            "site",
            site.id,
            1,
            {
                "site_id": str(site.id),
                "study_id": str(site.study_id),
                "name": site.name,
                "country": site.country,
                "state": site.state,
                "city": site.city,
                "status": site.status,
            },
        )
        return site

    def list(self, session: Session, study_id: UUID, principal: Principal) -> list[Site]:
        self._authorization.require(
            session,
            principal,
            "platform.membership.read",
            product="platform",
            resource_type="site",
            study_id=study_id,
        )
        if session.get(Study, study_id) is None:
            raise Problem(404, "study_not_found", "The study was not found")
        return list(
            session.scalars(
                select(Site)
                .where(Site.study_id == study_id, Site.status == "active")
                .order_by(Site.name, Site.id)
            )
        )


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
        target_user = session.get(User, user_id)
        if session.get(Study, study_id) is None or target_user is None:
            raise Problem(404, "membership_target_not_found", "The study or user was not found")
        if command.site_id is not None:
            site = session.get(Site, command.site_id)
            if site is None or site.study_id != study_id or site.status != "active":
                raise Problem(
                    422,
                    "invalid_membership_site",
                    "The selected active Site does not belong to this study",
                )
        role_ids = list(dict.fromkeys(command.role_ids))
        roles = list(session.scalars(select(Role).where(Role.id.in_(role_ids))))
        if len(roles) != len(role_ids):
            raise Problem(422, "role_not_found", "One or more roles do not exist")
        expected_scope = "site" if command.site_id is not None else "study"
        if any(
            role.product != command.product
            or role.scope_level != expected_scope
            or not role.assignable
            or role.status != "active"
            for role in roles
        ):
            raise Problem(
                422,
                "invalid_membership_role",
                "Every role must be an active, assignable "
                f"{expected_scope}-scope role for the selected product",
            )
        if (
            command.product == "platform"
            and command.site_id is None
            and PLATFORM_STUDY_OWNER_ROLE_ID not in role_ids
        ):
            self._require_another_study_owner(session, study_id, user_id)
        session.execute(
            delete(RoleAssignment).where(
                RoleAssignment.user_id == user_id,
                RoleAssignment.study_id == study_id,
                RoleAssignment.site_id == command.site_id,
                RoleAssignment.role_id.in_(select(Role.id).where(Role.product == command.product)),
            )
        )
        assignments = [
            RoleAssignment(
                user_id=user_id,
                role=role,
                user=target_user,
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
                "product": command.product,
                "site_id": str(command.site_id) if command.site_id else None,
                "role_ids": [str(item) for item in command.role_ids],
                "assigned_by": str(actor.id),
                "policy_version": version,
            },
        )
        return assignments

    def replace_system(
        self,
        session: Session,
        user_id: UUID,
        command: SystemAssignmentReplace,
        principal: Principal,
        actor: User,
    ) -> list[RoleAssignment]:
        self._authorization.require(
            session,
            principal,
            "platform.membership.manage",
            product="platform",
            resource_type="system_assignment",
            resource_id=str(user_id),
        )
        target_user = session.get(User, user_id)
        if target_user is None:
            raise Problem(404, "user_not_found", "The user was not found")
        role_ids = list(dict.fromkeys(command.role_ids))
        roles = list(session.scalars(select(Role).where(Role.id.in_(role_ids)))) if role_ids else []
        if len(roles) != len(role_ids):
            raise Problem(422, "role_not_found", "One or more roles do not exist")
        if any(
            role.product != command.product
            or role.scope_level != "system"
            or not role.assignable
            or role.status != "active"
            for role in roles
        ):
            raise Problem(
                422,
                "invalid_product_role",
                "Every role must be an active, assignable system role for the selected category",
            )
        session.execute(
            delete(RoleAssignment).where(
                RoleAssignment.user_id == user_id,
                RoleAssignment.study_id.is_(None),
                RoleAssignment.site_id.is_(None),
                RoleAssignment.role_id.in_(
                    select(Role.id).where(
                        Role.product == command.product,
                        Role.assignable.is_(True),
                    )
                ),
            )
        )
        assignments = [
            RoleAssignment(
                user_id=user_id,
                role_id=role.id,
                assigned_by=actor.id,
            )
            for role in roles
        ]
        session.add_all(assignments)
        version = _bump_policy(session)
        session.flush()
        _emit(
            session,
            "platform.assignment.changed.v1",
            "user",
            user_id,
            version,
            {
                "user_id": str(user_id),
                "issuer": target_user.issuer,
                "subject": target_user.subject,
                "product": command.product,
                "role_ids": [str(item) for item in role_ids],
                "assigned_by": str(actor.id),
                "policy_version": version,
            },
        )
        return assignments

    def list_system(
        self, session: Session, user_id: UUID, principal: Principal
    ) -> list[RoleAssignment]:
        self._authorization.require(
            session,
            principal,
            "platform.membership.read",
            product="platform",
            resource_type="system_assignment",
            resource_id=str(user_id),
        )
        return list(
            session.scalars(
                select(RoleAssignment)
                .join(Role, Role.id == RoleAssignment.role_id)
                .options(selectinload(RoleAssignment.role))
                .where(
                    RoleAssignment.user_id == user_id,
                    RoleAssignment.study_id.is_(None),
                    RoleAssignment.site_id.is_(None),
                )
                .order_by(Role.product, Role.name)
            )
        )

    def list_roles(self, session: Session, product: str | None, principal: Principal) -> list[Role]:
        self._authorization.require(
            session, principal, "platform.membership.read", product="platform", resource_type="role"
        )
        statement = select(Role).options(selectinload(Role.permissions))
        if product is not None:
            statement = statement.where(Role.product == product)
        return list(session.scalars(statement.order_by(Role.product, Role.name)))

    def list_permissions(
        self, session: Session, product: str | None, principal: Principal
    ) -> list[Permission]:
        self._authorization.require(
            session,
            principal,
            "platform.membership.read",
            product="platform",
            resource_type="permission",
        )
        statement = select(Permission)
        if product is not None:
            statement = statement.where(Permission.product == product)
        return list(session.scalars(statement.order_by(Permission.product, Permission.code)))

    def sync_product_permissions(
        self,
        session: Session,
        product: str,
        command: ProductPermissionSync,
        principal: Principal,
    ) -> list[Permission]:
        required_permission = {
            "aurinara": "aurinara.permission.sync",
            "aurinara-edc": "edc.permission.sync",
        }.get(product)
        if required_permission is None:
            raise Problem(422, "unsupported_product", "This product cannot register permissions")
        self._authorization.require(
            session,
            principal,
            required_permission,
            product=product,
            resource_type="permission_catalog",
        )
        codes = [item.code for item in command.permissions]
        if len(codes) != len(set(codes)):
            raise Problem(422, "duplicate_permission", "Permission codes must be unique")
        allowed_prefixes = {
            "aurinara": ("aurinara.",),
            "aurinara-edc": ("edc.", "ctms.", "pv."),
        }[product]
        if any(not code.startswith(allowed_prefixes) for code in codes):
            raise Problem(
                422,
                "invalid_permission_namespace",
                "Every permission code must use a namespace owned by the product",
            )

        changed = False
        registered: list[Permission] = []
        for definition in command.permissions:
            permission = session.get(Permission, definition.code)
            if permission is None:
                permission = Permission(code=definition.code, product=product)
                session.add(permission)
                changed = True
            elif permission.product != product:
                raise Problem(
                    409,
                    "permission_conflict",
                    "The permission code is already owned by another product",
                )
            values = {
                "product": product,
                "description": definition.description,
                "module": definition.module,
                "allowed_scope_levels": list(dict.fromkeys(definition.allowed_scope_levels)),
                "status": "active",
            }
            for field, value in values.items():
                if getattr(permission, field) != value:
                    setattr(permission, field, value)
                    changed = True
            registered.append(permission)

        registered_codes = set(codes)
        protected_permissions = {
            "aurinara": {"aurinara.permission.sync"},
            "aurinara-edc": {"edc.permission.sync", "edc.site.sync"},
        }[product]
        retired = list(
            session.scalars(
                select(Permission).where(
                    Permission.product == product,
                    Permission.code.not_in(protected_permissions),
                    Permission.code.not_in(registered_codes),
                    Permission.status == "active",
                )
            )
        )
        for permission in retired:
            permission.status = "inactive"
            changed = True

        if ensure_product_role_defaults(session, product):
            changed = True

        if changed:
            version = _bump_policy(session)
            actor = session.scalar(
                select(User).where(
                    User.issuer == principal.issuer,
                    User.subject == principal.subject,
                )
            )
            if actor is None:
                raise Problem(403, "identity_not_registered", "The identity is not registered")
            roles = list(
                session.scalars(
                    select(Role)
                    .options(selectinload(Role.permissions))
                    .where(Role.built_in.is_(True), Role.status == "active")
                )
            )
            for role in roles:
                permission_codes = role.permission_codes
                snapshot = self._role_snapshot(role, permission_codes)
                session.add(
                    RoleChange(
                        role_id=role.id,
                        actor_id=actor.id,
                        action="reconcile",
                        reason=f"Reconcile Platform roles after {product} permission sync",
                        previous_value=None,
                        new_value=snapshot,
                        policy_version=version,
                    )
                )
                self._emit_role_change(session, role, permission_codes, version)
            _emit(
                session,
                "platform.permissions.synced.v1",
                "product",
                uuid5(NAMESPACE_URL, f"aurinara-platform:product:{product}"),
                version,
                {
                    "product": product,
                    "permission_codes": sorted(registered_codes),
                    "policy_version": version,
                },
            )
        session.flush()
        return sorted(registered, key=lambda item: item.code)

    def create_role(
        self,
        session: Session,
        product: str,
        command: PlatformRoleCreate,
        principal: Principal,
        actor: User,
    ) -> Role:
        self._require_role_management(session, principal, product)
        if session.scalar(select(Role.id).where(Role.key == command.key)):
            raise Problem(409, "role_key_conflict", "A role with this key already exists")
        if session.scalar(
            select(Role.id).where(Role.product == product, Role.name == command.name)
        ):
            raise Problem(409, "role_name_conflict", "A role with this name already exists")
        permission_codes = self._validate_role_permissions(
            session, command.scope_level, command.permission_codes
        )
        role = Role(
            key=command.key,
            product=product,
            name=command.name,
            description=command.description,
            scope_level=command.scope_level,
            assignable=True,
            built_in=False,
            permission_policy="explicit",
            status="active",
            source_role_id=None,
        )
        session.add(role)
        session.flush()
        session.add_all(
            RolePermission(role_id=role.id, permission_code=code) for code in permission_codes
        )
        version = _bump_policy(session)
        session.flush()
        snapshot = self._role_snapshot(role, permission_codes)
        session.add(
            RoleChange(
                role_id=role.id,
                actor_id=actor.id,
                action="create",
                reason=command.reason,
                previous_value=None,
                new_value=snapshot,
                policy_version=version,
            )
        )
        self._emit_role_change(session, role, permission_codes, version)
        session.flush()
        session.expire(role, ["permissions"])
        return role

    def patch_role(
        self,
        session: Session,
        role_id: UUID,
        command: PlatformRolePatch,
        principal: Principal,
        actor: User,
        expected_revision: int,
    ) -> Role:
        role = session.scalar(
            select(Role).options(selectinload(Role.permissions)).where(Role.id == role_id)
        )
        if role is None:
            raise Problem(404, "role_not_found", "The role was not found")
        self._require_role_management(session, principal, role.product)
        if role.built_in:
            raise Problem(
                409,
                "built_in_role_managed",
                "Built-in roles are managed by Platform releases",
            )
        if role.revision != expected_revision:
            raise Problem(
                409,
                "role_revision_conflict",
                "The role changed after it was loaded; refresh before saving",
            )

        current_codes = role.permission_codes
        previous = self._role_snapshot(role, current_codes)
        next_scope = command.scope_level or role.scope_level
        next_codes = (
            self._validate_role_permissions(session, next_scope, command.permission_codes)
            if command.permission_codes is not None
            else current_codes
        )
        if (
            command.scope_level is not None
            and command.scope_level != role.scope_level
            and session.scalar(
                select(func.count())
                .select_from(RoleAssignment)
                .where(RoleAssignment.role_id == role.id)
            )
        ):
            raise Problem(
                409,
                "role_scope_in_use",
                "Remove existing assignments before changing role scope",
            )
        if command.name is not None and command.name != role.name:
            if session.scalar(
                select(Role.id).where(
                    Role.product == role.product,
                    Role.name == command.name,
                    Role.id != role.id,
                )
            ):
                raise Problem(409, "role_name_conflict", "A role with this name already exists")
            role.name = command.name
        if "description" in command.model_fields_set:
            role.description = command.description
        if command.scope_level is not None:
            role.scope_level = command.scope_level
        if command.status is not None:
            role.status = command.status
            role.assignable = command.status == "active"
        if command.permission_codes is not None:
            session.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
            session.add_all(
                RolePermission(role_id=role.id, permission_code=code) for code in next_codes
            )
        role.revision += 1
        role.updated_at = utc_now()
        version = _bump_policy(session)
        session.flush()
        snapshot = self._role_snapshot(role, next_codes)
        session.add(
            RoleChange(
                role_id=role.id,
                actor_id=actor.id,
                action="update" if role.status == "active" else "deactivate",
                reason=command.reason,
                previous_value=previous,
                new_value=snapshot,
                policy_version=version,
            )
        )
        self._emit_role_change(session, role, next_codes, version)
        session.flush()
        session.expire(role, ["permissions"])
        return role

    def list_role_changes(
        self, session: Session, role_id: UUID, principal: Principal
    ) -> list[RoleChange]:
        role = session.get(Role, role_id)
        if role is None:
            raise Problem(404, "role_not_found", "The role was not found")
        self._authorization.require(
            session,
            principal,
            "platform.membership.read",
            product="platform",
            resource_type="role_change",
            resource_id=str(role_id),
        )
        return list(
            session.scalars(
                select(RoleChange)
                .where(RoleChange.role_id == role_id)
                .order_by(RoleChange.created_at.desc(), RoleChange.id.desc())
            )
        )

    def _require_role_management(
        self, session: Session, principal: Principal, product: str
    ) -> None:
        self._authorization.require(
            session,
            principal,
            "platform.role.manage",
            product="platform",
            resource_type="role",
            resource_id=product,
        )

    @staticmethod
    def _validate_role_permissions(
        session: Session, scope_level: str, permission_codes: list[str]
    ) -> list[str]:
        codes = list(dict.fromkeys(permission_codes))
        permissions = list(session.scalars(select(Permission).where(Permission.code.in_(codes))))
        if len(permissions) != len(codes):
            raise Problem(422, "permission_not_found", "One or more permissions do not exist")
        if any(
            permission.status != "active" or scope_level not in permission.allowed_scope_levels
            for permission in permissions
        ):
            raise Problem(
                422,
                "invalid_role_permission",
                "Every permission must be active and allow the role scope",
            )
        return sorted(codes)

    @staticmethod
    def _role_snapshot(role: Role, permission_codes: list[str]) -> dict[str, object]:
        return {
            "id": str(role.id),
            "key": role.key,
            "source_role_id": str(role.source_role_id) if role.source_role_id else None,
            "product": role.product,
            "name": role.name,
            "description": role.description,
            "scope_level": role.scope_level,
            "assignable": role.assignable,
            "built_in": role.built_in,
            "permission_policy": role.permission_policy,
            "status": role.status,
            "revision": role.revision,
            "permission_codes": sorted(permission_codes),
        }

    @staticmethod
    def _emit_role_change(
        session: Session, role: Role, permission_codes: list[str], version: int
    ) -> None:
        if permission_codes != role.permission_codes:
            session.expire(role, ["permissions"])
        _emit_platform_role_change(session, role, version)

    def remove(
        self,
        session: Session,
        study_id: UUID,
        user_id: UUID,
        principal: Principal,
        product: str,
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
        if product == "platform" and site_id is None:
            self._require_another_study_owner(session, study_id, user_id)
        result = session.execute(
            delete(RoleAssignment).where(
                RoleAssignment.user_id == user_id,
                RoleAssignment.study_id == study_id,
                RoleAssignment.site_id == site_id,
                RoleAssignment.role_id.in_(select(Role.id).where(Role.product == product)),
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
                "product": product,
                "site_id": str(site_id) if site_id else None,
                "role_ids": [],
                "policy_version": version,
            },
        )

    @staticmethod
    def _require_another_study_owner(session: Session, study_id: UUID, user_id: UUID) -> None:
        target_is_owner = session.scalar(
            select(RoleAssignment.id).where(
                RoleAssignment.study_id == study_id,
                RoleAssignment.site_id.is_(None),
                RoleAssignment.user_id == user_id,
                RoleAssignment.role_id == PLATFORM_STUDY_OWNER_ROLE_ID,
            )
        )
        if target_is_owner is None:
            return
        owner_count = session.scalar(
            select(func.count())
            .select_from(RoleAssignment)
            .where(
                RoleAssignment.study_id == study_id,
                RoleAssignment.site_id.is_(None),
                RoleAssignment.role_id == PLATFORM_STUDY_OWNER_ROLE_ID,
            )
        )
        if owner_count is not None and owner_count <= 1:
            raise Problem(
                409,
                "last_study_owner",
                "Assign another Platform Study Owner before removing the final owner",
            )

    @staticmethod
    def list(session: Session, study_id: UUID) -> list[RoleAssignment]:
        return list(
            session.scalars(
                select(RoleAssignment)
                .options(
                    selectinload(RoleAssignment.role),
                    selectinload(RoleAssignment.user),
                )
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
        action = command.authorization_action or f"{command.owner_product}.object.upload"
        self._require_owned_action(session, action, command.owner_product)
        self._authorization.require(
            session,
            principal,
            action,
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
            upload_authorization_action=action,
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
            record.upload_authorization_action or f"{record.owner_product}.object.upload",
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

    def download(
        self,
        session: Session,
        object_id: UUID,
        principal: Principal,
        authorization_action: str | None = None,
    ) -> str:
        record = self._get(session, object_id)
        action = authorization_action or f"{record.owner_product}.object.download"
        self._require_owned_action(session, action, record.owner_product)
        self._authorization.require(
            session,
            principal,
            action,
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
        action = command.authorization_action or f"{command.product}.object.reference"
        self._require_owned_action(session, action, command.product)
        self._authorization.require(
            session,
            principal,
            action,
            product=command.product,
            resource_type="object",
            resource_id=str(record.id),
            study_id=record.study_id,
        )
        if command.idempotency_key is not None:
            idempotent = session.scalar(
                select(ObjectReference).where(
                    ObjectReference.product == command.product,
                    ObjectReference.idempotency_key == command.idempotency_key,
                )
            )
            if idempotent is not None:
                if (
                    idempotent.object_id == record.id
                    and idempotent.external_reference == command.external_reference
                    and idempotent.released_at is None
                ):
                    return idempotent
                raise Problem(
                    409,
                    "idempotency_conflict",
                    "The idempotency key was already used for different reference data",
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
            authorization_action=action,
            external_reference=command.external_reference,
            idempotency_key=command.idempotency_key,
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
            reference.authorization_action or f"{reference.product}.object.reference",
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
        repository_versions = session.scalar(
            select(func.count(RepositoryFileVersion.id)).where(
                RepositoryFileVersion.object_id == object_id
            )
        )
        if repository_versions:
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

    @staticmethod
    def _require_owned_action(session: Session, action: str, product: str) -> None:
        permission = session.get(Permission, action)
        if permission is None or permission.status != "active" or permission.product != product:
            raise Problem(
                422,
                "object_permission_invalid",
                "The declared object permission is not active for the owning product",
            )
