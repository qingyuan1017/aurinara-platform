"""Consume confirmed Cognito identities and register zero-access Platform users."""

from __future__ import annotations

import argparse

import boto3

from aurinara_platform.config import get_settings
from aurinara_platform.database import create_session_factory
from aurinara_platform.identity_registration import (
    backfill_confirmed_identities,
    process_registration_messages,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true", help="Continue polling for registrations")
    parser.add_argument("--backfill", action="store_true", help="Reconcile confirmed Cognito users")
    parser.add_argument("--user-pool-id")
    args = parser.parse_args()
    settings = get_settings()
    factory = create_session_factory(settings)
    if args.backfill:
        if not args.user_pool_id:
            raise SystemExit("--user-pool-id is required with --backfill")
        discovered, created = backfill_confirmed_identities(
            factory,
            boto3.client("cognito-idp", region_name=settings.object_store_region),
            user_pool_id=args.user_pool_id,
            issuer=settings.oidc_issuer,
        )
        print(f"discovered={discovered} created={created}")
        return
    if not settings.identity_registration_queue_url:
        raise SystemExit("IDENTITY_REGISTRATION_QUEUE_URL is required")
    client = boto3.client("sqs", region_name=settings.object_store_region)
    while True:
        count = process_registration_messages(
            factory,
            client,
            queue_url=settings.identity_registration_queue_url,
            expected_issuer=settings.oidc_issuer,
        )
        if not args.watch:
            print(f"processed={count}")
            return


if __name__ == "__main__":
    main()
