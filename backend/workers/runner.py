"""Generic Kafka consumer harness for any Worker implementation.

Per-message flow: consume -> StageProcessingService.handle() (idempotent
Postgres commit) -> commit the Kafka offset, strictly in that order. This
ordering is the entire crash-recovery guarantee:
  - Crash before the Postgres commit: nothing was persisted, offset was
    never committed, Kafka redelivers from the same point on restart.
  - Crash after the Postgres commit but before the offset commit: Kafka
    redelivers the same message, but StageProcessingService sees the
    Result already exists and treats it as a no-op.
Either way: no lost work, no duplicated Result.

enable_auto_commit=False is what makes this true — auto-commit would ack
the offset on a timer regardless of whether the message was actually
processed, which can lose work on a crash.
"""

import json
import logging
import uuid

from aiokafka import AIOKafkaConsumer
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.services.stage_processing_service import StageProcessingService
from backend.workers.base import StageMessage, Worker

logger = logging.getLogger(__name__)


class WorkerRunner:
    def __init__(
        self,
        worker: Worker,
        sessionmaker: async_sessionmaker,
        *,
        bootstrap_servers: str,
        topic: str,
        group_id: str,
    ) -> None:
        self._worker = worker
        self._sessionmaker = sessionmaker
        self._topic = topic
        self._group_id = group_id
        self._bootstrap_servers = bootstrap_servers
        self._consumer: AIOKafkaConsumer | None = None

    async def run_forever(self) -> None:
        # Constructed here, not in __init__: AIOKafkaConsumer needs a
        # running event loop at construction time, and __init__ runs before
        # asyncio.run() starts one.
        self._consumer = AIOKafkaConsumer(
            self._topic,
            bootstrap_servers=self._bootstrap_servers,
            group_id=self._group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )
        await self._consumer.start()
        logger.info(
            "worker %r consuming topic=%r group=%r",
            self._worker.name,
            self._topic,
            self._group_id,
        )
        try:
            async for record in self._consumer:
                await self._handle_record(record)
        finally:
            await self._consumer.stop()

    async def _handle_record(self, record) -> None:
        message = self._parse(record.value)

        async with self._sessionmaker() as session:
            try:
                await StageProcessingService(session, self._worker).handle(message)
            except Exception:
                logger.exception(
                    "job=%s stage=%s failed; leaving offset uncommitted for redelivery",
                    message.job_id,
                    message.stage,
                )
                return  # offset NOT committed -> Kafka redelivers this message

        await self._consumer.commit()

    @staticmethod
    def _parse(raw: bytes) -> StageMessage:
        data = json.loads(raw)
        return StageMessage(
            job_id=uuid.UUID(data["job_id"]),
            stage=data["stage"],
            workflow=data["workflow"],
            payload=data["payload"],
        )
