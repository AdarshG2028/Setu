"""Outbox Publisher: drains OutboxEvents into Kafka.

Guarantee: an event is marked published only after send_and_wait() returns
(i.e. the broker acked it). If the process dies between the ack and the
status update, the event is still 'pending' and gets republished on the next
poll — at-least-once delivery. A duplicate downstream is expected and must be
handled by idempotent consumers (see Result.UNIQUE(job_id, stage)); a lost
event is not acceptable and this ordering is what prevents it.

send_and_wait() is wrapped in a timeout, not called bare: a topic name
aiokafka rejects client-side (illegal characters) makes send_and_wait()
retry an internal "not a valid topic name" metadata refresh forever without
ever raising — unlike a broker-side rejection (e.g.
UnknownTopicOrPartitionError), which the broker returns promptly as a real
exception. Without the timeout, one bad topic name in a job submission
hangs poll_once()'s sequential loop forever, wedging the whole publisher —
not just that one event, every event behind it too, indefinitely. A
TimeoutError is handled by the same mark_failed/max_attempts path as any
other publish failure.
"""

import asyncio
import json
import logging

from aiokafka import AIOKafkaProducer
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.repositories.outbox_repository import OutboxRepository

logger = logging.getLogger(__name__)


class OutboxPublisher:
    def __init__(
        self,
        sessionmaker: async_sessionmaker,
        producer: AIOKafkaProducer,
        *,
        poll_interval_seconds: float,
        batch_size: int,
        max_publish_attempts: int,
        publish_timeout_seconds: float = 10.0,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._producer = producer
        self._poll_interval_seconds = poll_interval_seconds
        self._batch_size = batch_size
        self._max_publish_attempts = max_publish_attempts
        self._publish_timeout_seconds = publish_timeout_seconds
        self._started = False

    async def ensure_started(self) -> None:
        """Connects to Kafka lazily so a broker outage never blocks app startup.

        Jobs still get written to the outbox while Kafka is down; this just
        keeps retrying until it can start draining them.
        """
        if not self._started:
            await self._producer.start()
            self._started = True

    async def stop(self) -> None:
        if self._started:
            await self._producer.stop()

    async def poll_once(self) -> int:
        """Publish one batch of pending events. Returns the count published."""
        async with self._sessionmaker() as session:
            repo = OutboxRepository(session)
            events = await repo.fetch_unpublished_batch(self._batch_size)

            published = 0
            for event in events:
                try:
                    await asyncio.wait_for(
                        self._producer.send_and_wait(
                            event.topic,
                            key=event.partition_key.encode("utf-8"),
                            value=json.dumps(event.payload).encode("utf-8"),
                        ),
                        timeout=self._publish_timeout_seconds,
                    )
                except Exception as exc:  # broker down, topic misconfigured, timed out, etc.
                    logger.warning(
                        "outbox publish failed: %s",
                        exc,
                        extra={
                            "job_id": str(event.aggregate_id),
                            "outbox_event_id": str(event.id),
                            "topic": event.topic,
                        },
                    )
                    await repo.mark_failed(
                        event.id, str(exc), max_attempts=self._max_publish_attempts
                    )
                    await session.commit()
                    continue

                # Ack has landed — only now is it safe to mark published.
                await repo.mark_published(event.id)
                await session.commit()
                published += 1
                logger.info(
                    "outbox event published",
                    extra={
                        "job_id": str(event.aggregate_id),
                        "outbox_event_id": str(event.id),
                        "topic": event.topic,
                    },
                )

            return published

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await self.ensure_started()
                await self.poll_once()
            except Exception:
                logger.exception("outbox publisher poll iteration failed")
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=self._poll_interval_seconds
                )
            except asyncio.TimeoutError:
                pass
