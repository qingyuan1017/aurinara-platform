"""At-least-once publication of committed control-plane events."""

import json
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

import boto3
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from aurinara_platform.config import Settings
from aurinara_platform.models import OutboxEvent


class EventPublisher(Protocol):
    def publish(self, event: OutboxEvent) -> None: ...


class EventBridgePublisher:
    def __init__(self, settings: Settings) -> None:
        if not settings.eventbridge_bus_name:
            raise ValueError("event bus name is required")
        self._bus = settings.eventbridge_bus_name
        self._source = settings.eventbridge_source
        self._client = boto3.client(
            "events",
            region_name=settings.object_store_region,
            endpoint_url=settings.eventbridge_endpoint_url,
        )

    def publish(self, event: OutboxEvent) -> None:
        envelope = {
            "id": str(event.id),
            "type": event.event_type,
            "occurred_at": event.occurred_at.isoformat(),
            "aggregate_type": event.aggregate_type,
            "aggregate_id": str(event.aggregate_id),
            "sequence": event.sequence,
            "data": event.payload,
        }
        result = self._client.put_events(
            Entries=[
                {
                    "Source": self._source,
                    "DetailType": event.event_type,
                    "Detail": json.dumps(envelope, separators=(",", ":")),
                    "EventBusName": self._bus,
                }
            ]
        )
        if result.get("FailedEntryCount"):
            raise RuntimeError("event publication failed")


def publish_once(
    factory: sessionmaker[Session], publisher: EventPublisher, *, limit: int = 100
) -> int:
    """Publish without holding a database transaction during network I/O.

    Crashes may publish an event more than once. Event IDs are immutable and
    consumers must deduplicate them.
    """

    with factory() as session:
        event_ids = list(
            session.scalars(
                select(OutboxEvent.id)
                .where(OutboxEvent.published_at.is_(None))
                .order_by(OutboxEvent.occurred_at, OutboxEvent.id)
                .limit(limit)
            )
        )
    published = 0
    for event_id in event_ids:
        with factory() as session:
            event = session.get(OutboxEvent, UUID(str(event_id)))
            if event is None or event.published_at is not None:
                continue
            session.expunge(event)
        publisher.publish(event)
        with factory.begin() as session:
            result = session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == event.id, OutboxEvent.published_at.is_(None))
                .values(published_at=datetime.now(UTC))
            )
            if result.rowcount:
                published += 1
    return published
