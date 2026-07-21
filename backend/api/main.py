"""FastAPI application factory and ASGI entry point."""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from prometheus_client import make_asgi_app

from backend.api.routes import health, jobs
from backend.core.config import Settings, get_settings
from backend.database.session import get_sessionmaker
from backend.messaging.kafka_producer import build_producer
from backend.messaging.outbox_publisher import OutboxPublisher
from backend.observability.logging import configure_logging
from backend.observability.metrics import poll_job_lifecycle_gauges

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Starts the outbox publisher and the job-lifecycle metrics poller as
    background tasks.

    The publisher connects to Kafka lazily (see
    OutboxPublisher.ensure_started), so a Kafka outage at boot never blocks
    the API from serving requests — jobs still get written to the outbox
    and drain once the broker is reachable.
    """
    settings = get_settings()
    publisher = OutboxPublisher(
        get_sessionmaker(),
        build_producer(),
        poll_interval_seconds=settings.outbox_poll_interval_seconds,
        batch_size=settings.outbox_batch_size,
        max_publish_attempts=settings.outbox_max_publish_attempts,
        publish_timeout_seconds=settings.outbox_publish_timeout_seconds,
    )
    stop_event = asyncio.Event()
    publisher_task = asyncio.create_task(publisher.run_forever(stop_event))
    metrics_task = asyncio.create_task(
        poll_job_lifecycle_gauges(
            get_sessionmaker(), stop_event, settings.metrics_poll_interval_seconds
        )
    )

    try:
        yield
    finally:
        stop_event.set()
        await publisher_task
        await metrics_task
        await publisher.stop()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(jobs.router)
    app.mount("/metrics", make_asgi_app())
    return app


app = create_app()
