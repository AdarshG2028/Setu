"""Processes one dispatched stage: idempotent Result write + Job status
update, in a single Postgres commit.

The crash-recovery guarantee lives at the boundary between this and the
Kafka consumer harness (workers/runner.py), not inside either one alone:
this commits to Postgres FIRST; the harness commits the Kafka offset only
after this returns without raising. A crash between those two commits
means Kafka redelivers a message whose Result already exists — handled
here as a no-op, not reprocessed and not duplicated.
"""

import datetime as dt
import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import JobStatus, Result, WorkerExecution
from backend.models.enums import ExecutionStatus
from backend.repositories.job_repository import JobRepository
from backend.repositories.result_repository import ResultRepository
from backend.repositories.worker_execution_repository import WorkerExecutionRepository
from backend.workers.base import StageMessage, Worker

logger = logging.getLogger(__name__)


class StageProcessingService:
    def __init__(self, session: AsyncSession, worker: Worker) -> None:
        self._session = session
        self._worker = worker
        self._jobs = JobRepository(session)
        self._results = ResultRepository(session)
        self._executions = WorkerExecutionRepository(session)

    async def handle(self, message: StageMessage) -> None:
        """Raises only on failures that should block the Kafka offset commit
        (the worker's own exception, or a genuine DB problem). Returns
        normally — including for an already-processed redelivery — whenever
        it's safe for the caller to commit the offset and move on.
        """
        existing = await self._results.get(message.job_id, message.stage)
        if existing is not None:
            logger.info(
                "job=%s stage=%s already has a result; redelivery, skipping",
                message.job_id,
                message.stage,
            )
            return

        job = await self._jobs.get(message.job_id)
        if job is None:
            logger.error("job %s not found; dropping message", message.job_id)
            return  # nothing to do with an orphaned message; don't block the partition

        if job.status == JobStatus.PENDING:
            job.status = JobStatus.RUNNING
            job.started_at = dt.datetime.now(dt.UTC)

        attempt = job.attempts + 1

        try:
            result_payload = await self._worker.process(message)
        except Exception as exc:
            job.attempts = attempt
            job.last_error = str(exc)[:2000]
            self._executions.add(
                WorkerExecution(
                    job_id=job.id,
                    worker_name=self._worker.name,
                    stage=message.stage,
                    attempt=attempt,
                    status=ExecutionStatus.FAILED,
                    error=str(exc)[:2000],
                    finished_at=dt.datetime.now(dt.UTC),
                )
            )
            await self._session.commit()
            raise

        self._results.add(
            Result(
                job_id=job.id,
                worker_name=self._worker.name,
                stage=message.stage,
                payload=result_payload,
            )
        )
        self._executions.add(
            WorkerExecution(
                job_id=job.id,
                worker_name=self._worker.name,
                stage=message.stage,
                attempt=attempt,
                status=ExecutionStatus.SUCCEEDED,
                finished_at=dt.datetime.now(dt.UTC),
            )
        )
        job.attempts = attempt
        job.current_stage = message.stage + 1
        is_last_stage = message.stage == len(message.workflow) - 1
        if is_last_stage:
            # Phase 2 scope: no engine yet to dispatch further stages, so a
            # job is only "done" when its last stage succeeds. Earlier
            # stages leave it RUNNING — correct today for single-stage demo
            # workflows, and forward-compatible with Phase 4's engine
            # picking up current_stage for the rest.
            job.status = JobStatus.COMPLETED
            job.completed_at = dt.datetime.now(dt.UTC)

        try:
            await self._session.commit()
        except IntegrityError:
            # Someone else's delivery of the same message already committed
            # a Result for this (job_id, stage) between our check above and
            # this commit. Their write stands; ours is redundant, not wrong.
            await self._session.rollback()
