"""Durable provisioning of confirmed Cognito identities with create-only access."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from aurinara_platform.default_access import (
    REGISTERED_STUDY_CREATOR_ROLE_ID,
    ensure_platform_access_defaults,
    ensure_registered_study_creator,
)
from aurinara_platform.models import OutboxEvent, PolicyState, User, UserStatus, utc_now

logger = logging.getLogger(__name__)


class ConfirmedIdentityEvent(BaseModel):
    """Minimal, versioned identity data emitted by the Cognito confirmation trigger."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    event_id: str = Field(min_length=1, max_length=255)
    issuer: str = Field(min_length=1, max_length=512)
    subject: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=320)
    display_name: str | None = Field(default=None, max_length=255)
    confirmed_at: datetime


class SqsClient(Protocol):
    """Subset of the SQS client used by the registration worker."""

    def receive_message(self, **kwargs: object) -> Mapping[str, object]: ...

    def delete_message(self, **kwargs: object) -> object: ...


class CognitoClient(Protocol):
    """Subset of the Cognito client used for deployment-time reconciliation."""

    def list_users(self, **kwargs: object) -> Mapping[str, object]: ...


def provision_confirmed_identity(
    session: Session,
    event: ConfirmedIdentityEvent,
    *,
    expected_issuer: str,
) -> tuple[User, bool]:
    """Create a confirmed identity and ensure its create-only baseline role."""

    if event.schema_version != "1.0.0":
        raise ValueError("unsupported confirmed identity event schema")
    if event.issuer != expected_issuer:
        raise ValueError("confirmed identity issuer does not match this environment")

    # Cognito can retry a confirmation trigger, and multiple workers can receive
    # duplicate messages. The singleton row serializes the short create/recheck
    # section without changing the authorization policy version.
    state = session.scalar(select(PolicyState).where(PolicyState.id == 1).with_for_update())
    if state is None:
        raise RuntimeError("Platform policy state is not initialized")

    defaults_changed = ensure_platform_access_defaults(session)
    user = session.scalar(
        select(User).where(User.issuer == event.issuer, User.subject == event.subject)
    )
    created = user is None
    if user is None:
        user = User(
            issuer=event.issuer,
            subject=event.subject,
            email=event.email,
            display_name=event.display_name or event.email,
            status=UserStatus.active,
        )
        session.add(user)
        session.flush()
        session.add(
            OutboxEvent(
                event_type="platform.user.created.v1",
                aggregate_type="user",
                aggregate_id=user.id,
                sequence=1,
                payload={
                    "user_id": str(user.id),
                    "issuer": user.issuer,
                    "subject": user.subject,
                    "email": user.email or "",
                    "display_name": user.display_name or "",
                    "status": user.status,
                },
            )
        )

    access_changed = False
    if user.status == UserStatus.active:
        access_changed = ensure_registered_study_creator(session, user)
    if defaults_changed or access_changed:
        state.version += 1
        state.updated_at = utc_now()
    if access_changed:
        session.add(
            OutboxEvent(
                event_type="platform.assignment.changed.v1",
                aggregate_type="user",
                aggregate_id=user.id,
                sequence=state.version,
                payload={
                    "user_id": str(user.id),
                    "issuer": user.issuer,
                    "subject": user.subject,
                    "product": "platform",
                    "role_ids": [str(REGISTERED_STUDY_CREATOR_ROLE_ID)],
                    "assigned_by": str(user.id),
                    "policy_version": state.version,
                },
            )
        )
    session.flush()
    return user, created


def process_registration_messages(
    factory: sessionmaker[Session],
    client: SqsClient,
    *,
    queue_url: str,
    expected_issuer: str,
    wait_time_seconds: int = 20,
    max_messages: int = 10,
) -> int:
    """Process one bounded SQS batch and acknowledge only committed identities."""

    response = client.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=max_messages,
        WaitTimeSeconds=wait_time_seconds,
        AttributeNames=["ApproximateReceiveCount"],
    )
    messages = response.get("Messages", [])
    if not isinstance(messages, list):
        raise RuntimeError("SQS returned an invalid Messages collection")

    processed = 0
    for message in messages:
        if not isinstance(message, Mapping):
            logger.warning("identity_registration_invalid_message")
            continue
        receipt = message.get("ReceiptHandle")
        body = message.get("Body")
        if not isinstance(receipt, str) or not isinstance(body, str):
            logger.warning("identity_registration_invalid_message")
            continue
        try:
            event = ConfirmedIdentityEvent.model_validate(json.loads(body))
            with factory.begin() as session:
                _, created = provision_confirmed_identity(
                    session,
                    event,
                    expected_issuer=expected_issuer,
                )
            client.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
            processed += 1
            logger.info(
                "identity_registration_processed",
                extra={"created": created, "event_id": event.event_id},
            )
        except (json.JSONDecodeError, ValidationError, ValueError):
            # Leave invalid input for the bounded queue redrive policy. Never log
            # the body because it contains identity data.
            logger.warning("identity_registration_rejected")
        except Exception:
            logger.exception("identity_registration_failed")
    return processed


def backfill_confirmed_identities(
    factory: sessionmaker[Session],
    client: CognitoClient,
    *,
    user_pool_id: str,
    issuer: str,
) -> tuple[int, int]:
    """Reconcile every confirmed, enabled Cognito user in one database transaction."""

    events: list[ConfirmedIdentityEvent] = []
    pagination_token: str | None = None
    while True:
        request: dict[str, object] = {"UserPoolId": user_pool_id, "Limit": 60}
        if pagination_token:
            request["PaginationToken"] = pagination_token
        response = client.list_users(**request)
        users = response.get("Users", [])
        if not isinstance(users, list):
            raise RuntimeError("Cognito returned an invalid Users collection")
        for item in users:
            if not isinstance(item, Mapping):
                raise RuntimeError("Cognito returned an invalid user")
            if item.get("Enabled") is False or item.get("UserStatus") != "CONFIRMED":
                continue
            attributes = item.get("Attributes", [])
            if not isinstance(attributes, list):
                raise RuntimeError("Cognito returned invalid user attributes")
            values = {
                str(attribute.get("Name")): attribute.get("Value")
                for attribute in attributes
                if isinstance(attribute, Mapping)
            }
            subject = values.get("sub")
            email = values.get("email")
            if not isinstance(subject, str) or not isinstance(email, str):
                raise RuntimeError("a confirmed Cognito user is missing sub or email")
            created_at = item.get("UserCreateDate")
            events.append(
                ConfirmedIdentityEvent(
                    schema_version="1.0.0",
                    event_id=f"backfill:{subject}",
                    issuer=issuer,
                    subject=subject,
                    email=email,
                    display_name=(
                        values.get("name") if isinstance(values.get("name"), str) else email
                    ),
                    confirmed_at=(
                        created_at if isinstance(created_at, datetime) else datetime.now(UTC)
                    ),
                )
            )
        token = response.get("PaginationToken")
        pagination_token = token if isinstance(token, str) and token else None
        if pagination_token is None:
            break

    created = 0
    with factory.begin() as session:
        for event in events:
            _, changed = provision_confirmed_identity(
                session,
                event,
                expected_issuer=issuer,
            )
            created += int(changed)
    return len(events), created
