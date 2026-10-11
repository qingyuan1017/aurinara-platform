"""Import a reviewed, versioned control-plane snapshot idempotently.

The importer deliberately consumes an exported JSON file rather than opening a
product database. Conflicts are reported and abort the transaction; records are
never heuristically merged.
"""

import argparse
import json
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from aurinara_platform.config import get_settings
from aurinara_platform.database import create_session_factory
from aurinara_platform.models import (
    OutboxEvent,
    Permission,
    PolicyState,
    Role,
    RoleAssignment,
    RolePermission,
    Study,
    User,
)


class SnapshotUser(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    issuer: str
    subject: str
    email: str | None = None
    display_name: str | None = None
    status: str = "active"


class SnapshotStudy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    code: str
    display_name: str
    status: str = "active"
    created_by: UUID


class SnapshotRole(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    product: str
    name: str
    description: str | None = None
    permissions: list[str] = Field(default_factory=list)


class SnapshotAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    user_id: UUID
    role_id: UUID
    study_id: UUID | None = None
    site_id: UUID | None = None
    assigned_by: UUID


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str
    users: list[SnapshotUser]
    studies: list[SnapshotStudy]
    roles: list[SnapshotRole]
    assignments: list[SnapshotAssignment]


def _same(actual: object, expected: dict[str, object]) -> bool:
    return all(getattr(actual, key) == value for key, value in expected.items())


def import_snapshot(path: Path) -> None:
    snapshot = Snapshot.model_validate(json.loads(path.read_text(encoding="utf-8")))
    if snapshot.schema_version != "1.0.0":
        raise ValueError("unsupported snapshot schema version")
    factory = create_session_factory(get_settings())
    with factory.begin() as session:
        for source in snapshot.users:
            values = source.model_dump()
            existing = session.get(User, source.id)
            by_subject = session.scalar(
                select(User).where(User.issuer == source.issuer, User.subject == source.subject)
            )
            if existing is not None and not _same(existing, values):
                raise ValueError(f"user conflict: {source.id}")
            if by_subject is not None and by_subject.id != source.id:
                raise ValueError(f"identity conflict: {source.issuer} {source.subject}")
            if existing is None:
                session.add(User(**values))
                session.add(
                    OutboxEvent(
                        event_type="platform.user.created.v1",
                        aggregate_type="user",
                        aggregate_id=source.id,
                        sequence=1,
                        payload={
                            "user_id": str(source.id),
                            "issuer": source.issuer,
                            "subject": source.subject,
                            "email": source.email or "",
                            "display_name": source.display_name or "",
                            "status": source.status,
                        },
                    )
                )
        session.flush()

        for source in snapshot.studies:
            values = source.model_dump()
            existing = session.get(Study, source.id)
            by_code = session.scalar(select(Study).where(Study.code == source.code))
            if existing is not None and not _same(existing, values):
                raise ValueError(f"study conflict: {source.id}")
            if by_code is not None and by_code.id != source.id:
                raise ValueError(f"study-code conflict: {source.code}")
            if existing is None:
                session.add(Study(**values))
                session.add(
                    OutboxEvent(
                        event_type="platform.study.created.v1",
                        aggregate_type="study",
                        aggregate_id=source.id,
                        sequence=1,
                        payload={
                            "study_id": str(source.id),
                            "code": source.code,
                            "display_name": source.display_name,
                            "status": source.status,
                            "created_by": str(source.created_by),
                        },
                    )
                )
        session.flush()

        for source in snapshot.roles:
            role_values = source.model_dump(exclude={"permissions"})
            existing = session.get(Role, source.id)
            if existing is not None and not _same(existing, role_values):
                raise ValueError(f"role conflict: {source.id}")
            role = existing or Role(**role_values)
            if existing is None:
                session.add(role)
            for code in source.permissions:
                product = code.split(".", 1)[0]
                permission = session.get(Permission, code)
                if permission is None:
                    session.add(Permission(code=code, product=product))
                if session.get(RolePermission, (source.id, code)) is None:
                    session.add(RolePermission(role_id=source.id, permission_code=code))
        session.flush()

        changed_scopes: set[tuple[UUID, UUID, UUID | None]] = set()
        for source in snapshot.assignments:
            values = source.model_dump()
            existing = session.get(RoleAssignment, source.id)
            if existing is not None and not _same(existing, values):
                raise ValueError(f"assignment conflict: {source.id}")
            if existing is None:
                session.add(RoleAssignment(**values))
                if source.study_id is not None:
                    changed_scopes.add((source.study_id, source.user_id, source.site_id))
        if changed_scopes:
            policy = session.get(PolicyState, 1) or PolicyState(id=1, version=1)
            if policy not in session:
                session.add(policy)
            policy.version += 1
            session.flush()
            for study_id, user_id, site_id in changed_scopes:
                scoped = list(
                    session.scalars(
                        select(RoleAssignment).where(
                            RoleAssignment.study_id == study_id,
                            RoleAssignment.user_id == user_id,
                            RoleAssignment.site_id == site_id,
                        )
                    )
                )
                session.add(
                    OutboxEvent(
                        event_type="platform.membership.changed.v1",
                        aggregate_type="study",
                        aggregate_id=study_id,
                        sequence=policy.version,
                        payload={
                            "study_id": str(study_id),
                            "user_id": str(user_id),
                            "site_id": str(site_id) if site_id else None,
                            "role_ids": [str(item.role_id) for item in scoped],
                            "assigned_by": str(scoped[0].assigned_by),
                            "policy_version": policy.version,
                        },
                    )
                )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    import_snapshot(args.snapshot)


if __name__ == "__main__":
    main()
