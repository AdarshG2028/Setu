"""`python -m backend.workers.cli <topic> [--worker dummy] [--group-id ...] [--metrics-port ...]`

Deliberately a standalone process, not a background task inside the API's
lifespan: the crash-recovery guarantee has to survive a real SIGKILL of
just the worker, and asyncio task cancellation isn't a real crash. It also
matches the spec's "independently deployable worker" requirement.

Each worker process runs its own Prometheus metrics HTTP server rather
than sharing one with the API: a worker is an independent OS process (that
independence is the whole point, see above), so it has its own in-memory
metrics registry that only the API's registry can't see and vice versa.
Running two workers on one host needs two different --metrics-port values.
"""

import argparse
import asyncio

from prometheus_client import start_http_server

from backend.core.config import get_settings
from backend.database.session import get_sessionmaker
from backend.observability.logging import configure_logging
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
    parser.add_argument(
        "--metrics-port",
        type=int,
        default=9100,
        help="Port for this worker's own Prometheus /metrics server (default: 9100)",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings)
    start_http_server(args.metrics_port)

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
