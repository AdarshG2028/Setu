"""`python -m backend.workers.cli <topic> [--worker dummy] [--group-id ...]`

Deliberately a standalone process, not a background task inside the API's
lifespan: the crash-recovery guarantee has to survive a real SIGKILL of
just the worker, and asyncio task cancellation isn't a real crash. It also
matches the spec's "independently deployable worker" requirement.
"""

import argparse
import asyncio
import logging

from backend.core.config import get_settings
from backend.database.session import get_sessionmaker
from backend.workers.dummy_worker import DummyWorker
from backend.workers.runner import WorkerRunner

WORKERS = {"dummy": DummyWorker}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a Setu stage worker.")
    parser.add_argument("topic", help="Kafka topic to consume (usually the stage name)")
    parser.add_argument("--worker", default="dummy", choices=sorted(WORKERS))
    parser.add_argument(
        "--group-id", default=None, help="Kafka consumer group (default: setu-<topic>-workers)"
    )
    args = parser.parse_args()

    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    worker = WORKERS[args.worker]()
    runner = WorkerRunner(
        worker,
        get_sessionmaker(),
        bootstrap_servers=settings.kafka_bootstrap_servers,
        topic=args.topic,
        group_id=args.group_id or f"setu-{args.topic}-workers",
        retry_base_delay_seconds=settings.worker_retry_base_delay_seconds,
        retry_max_delay_seconds=settings.worker_retry_max_delay_seconds,
    )
    asyncio.run(runner.run_forever())


if __name__ == "__main__":
    main()
