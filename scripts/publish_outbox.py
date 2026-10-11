"""Publish one bounded batch of committed outbox events."""

import argparse
import time

from aurinara_platform.config import get_settings
from aurinara_platform.database import create_session_factory
from aurinara_platform.outbox import EventBridgePublisher, publish_once


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true", help="Continue polling the outbox")
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    args = parser.parse_args()
    settings = get_settings()
    factory = create_session_factory(settings)
    publisher = EventBridgePublisher(settings)
    while True:
        count = publish_once(factory, publisher)
        if not args.watch:
            print(f"published={count}")
            return
        if count == 0:
            time.sleep(max(args.interval_seconds, 0.1))


if __name__ == "__main__":
    main()
