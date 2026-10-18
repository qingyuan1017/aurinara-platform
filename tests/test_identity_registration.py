import json
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aurinara_platform.identity_registration import (
    ConfirmedIdentityEvent,
    backfill_confirmed_identities,
    process_registration_messages,
    provision_confirmed_identity,
)
from aurinara_platform.models import OutboxEvent, PolicyState, RoleAssignment, User


def _event() -> ConfirmedIdentityEvent:
    return ConfirmedIdentityEvent(
        schema_version="1.0.0",
        event_id="confirmation-1",
        issuer="https://issuer.example",
        subject="immutable-cognito-subject",
        email="new.user@example.test",
        display_name="New User",
        confirmed_at=datetime(2026, 10, 18, tzinfo=UTC),
    )


def test_confirmed_identity_is_created_once_with_no_access(session: Session) -> None:
    session.add(PolicyState(id=1, version=7))
    session.commit()

    first, first_created = provision_confirmed_identity(
        session, _event(), expected_issuer="https://issuer.example"
    )
    session.commit()
    second, second_created = provision_confirmed_identity(
        session, _event(), expected_issuer="https://issuer.example"
    )
    session.commit()

    assert first_created is True
    assert second_created is False
    assert second.id == first.id
    assert session.scalar(select(func.count()).select_from(User)) == 1
    assert session.scalar(select(func.count()).select_from(RoleAssignment)) == 0
    assert session.get(PolicyState, 1).version == 7
    event = session.scalar(select(OutboxEvent))
    assert event is not None
    assert event.event_type == "platform.user.created.v1"
    assert event.payload["subject"] == "immutable-cognito-subject"


class FakeSqs:
    def __init__(self, body: str) -> None:
        self.body = body
        self.deleted: list[dict[str, str]] = []

    def receive_message(self, **kwargs: object) -> dict[str, object]:
        return {"Messages": [{"Body": self.body, "ReceiptHandle": "receipt-1"}]}

    def delete_message(self, **kwargs: object) -> None:
        self.deleted.append(kwargs)  # type: ignore[arg-type]


def test_worker_acknowledges_only_after_commit(session_factory) -> None:
    with session_factory.begin() as session:
        session.add(PolicyState(id=1, version=1))
    client = FakeSqs(_event().model_dump_json())

    assert process_registration_messages(
        session_factory,
        client,
        queue_url="https://sqs.example/registration",
        expected_issuer="https://issuer.example",
        wait_time_seconds=0,
    ) == 1
    assert client.deleted == [
        {"QueueUrl": "https://sqs.example/registration", "ReceiptHandle": "receipt-1"}
    ]


def test_worker_leaves_invalid_messages_for_bounded_redrive(session_factory) -> None:
    client = FakeSqs(json.dumps({"schema_version": "1.0.0", "email": "missing-identity"}))

    assert process_registration_messages(
        session_factory,
        client,
        queue_url="https://sqs.example/registration",
        expected_issuer="https://issuer.example",
        wait_time_seconds=0,
    ) == 0
    assert client.deleted == []


class FakeCognito:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def list_users(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        if "PaginationToken" not in kwargs:
            return {
                "Users": [
                    {
                        "Enabled": True,
                        "UserStatus": "CONFIRMED",
                        "UserCreateDate": datetime(2026, 10, 18, tzinfo=UTC),
                        "Attributes": [
                            {"Name": "sub", "Value": "confirmed-subject"},
                            {"Name": "email", "Value": "confirmed@example.test"},
                        ],
                    },
                    {
                        "Enabled": True,
                        "UserStatus": "UNCONFIRMED",
                        "Attributes": [
                            {"Name": "sub", "Value": "unconfirmed-subject"},
                            {"Name": "email", "Value": "unconfirmed@example.test"},
                        ],
                    },
                ],
                "PaginationToken": "page-2",
            }
        return {
            "Users": [
                {
                    "Enabled": False,
                    "UserStatus": "CONFIRMED",
                    "Attributes": [
                        {"Name": "sub", "Value": "disabled-subject"},
                        {"Name": "email", "Value": "disabled@example.test"},
                    ],
                }
            ]
        }


def test_backfill_imports_only_confirmed_enabled_users(session_factory) -> None:
    with session_factory.begin() as session:
        session.add(PolicyState(id=1, version=1))
    client = FakeCognito()

    assert backfill_confirmed_identities(
        session_factory,
        client,
        user_pool_id="pool-1",
        issuer="https://issuer.example",
    ) == (1, 1)
    assert backfill_confirmed_identities(
        session_factory,
        client,
        user_pool_id="pool-1",
        issuer="https://issuer.example",
    ) == (1, 0)

    with session_factory() as session:
        users = list(session.scalars(select(User)))
        assert [item.subject for item in users] == ["confirmed-subject"]
        assert session.scalar(select(func.count()).select_from(RoleAssignment)) == 0
