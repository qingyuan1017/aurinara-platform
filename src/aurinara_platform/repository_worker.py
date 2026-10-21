"""Durable Repository job worker."""

from __future__ import annotations

import argparse
import json
import logging
import socket
import time

from sqlalchemy import func, select

from aurinara_platform.config import get_settings
from aurinara_platform.database import create_session_factory
from aurinara_platform.main import create_app
from aurinara_platform.models import RepositoryJob, utc_now

logger = logging.getLogger(__name__)


def _emit_metrics(session, environment: str) -> None:
    backlog = (
        session.scalar(
            select(func.count(RepositoryJob.id)).where(
                RepositoryJob.state.in_(["queued", "running"])
            )
        )
        or 0
    )
    oldest = session.scalar(
        select(func.min(RepositoryJob.created_at)).where(
            RepositoryJob.state.in_(["queued", "running"])
        )
    )
    age = max(0, int((utc_now() - oldest).total_seconds())) if oldest else 0
    logger.info(
        json.dumps(
            {
                "_aws": {
                    "Timestamp": int(time.time() * 1000),
                    "CloudWatchMetrics": [
                        {
                            "Namespace": "Aurinara Platform",
                            "Dimensions": [["Environment"]],
                            "Metrics": [
                                {"Name": "RepositoryJobBacklog", "Unit": "Count"},
                                {"Name": "RepositoryOldestJobSeconds", "Unit": "Seconds"},
                            ],
                        }
                    ],
                },
                "Environment": environment,
                "RepositoryJobBacklog": backlog,
                "RepositoryOldestJobSeconds": age,
            }
        )
    )


def run(*, once: bool = False) -> int:
    settings = get_settings()
    session_factory = create_session_factory(settings)
    app = create_app(settings, session_factory=session_factory)
    service = app.state.rich_repository_service
    worker_id = socket.gethostname()
    processed = 0
    while True:
        with session_factory.begin() as session:
            job = service.claim_job(session, worker_id)
        if job is None:
            if once:
                return processed
            time.sleep(2)
            continue
        try:
            with session_factory.begin() as session:
                claimed = session.get(type(job), job.id)
                if claimed is not None and claimed.state == "running":
                    service.execute_job(session, claimed)
                    processed += 1
                    _emit_metrics(session, settings.environment)
        except Exception:
            logger.exception("Repository job %s failed", job.id)
        if once:
            return processed


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    arguments = parser.parse_args()
    raise SystemExit(0 if run(once=arguments.once) >= 0 else 1)


if __name__ == "__main__":
    main()
