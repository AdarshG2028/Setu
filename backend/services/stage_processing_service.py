"""Processes one dispatched stage: idempotent Result write + Job status
update, in a single Postgres commit.

The crash-recovery guarantee lives at the boundary between this and the
Kafka consumer harness (workers/runner.py), not inside either one alone:
this commits to Postgres FIRST; the harness commits the Kafka offset only
after this returns. A crash between those two commits means Kafka
redelivers a message whose Result already exists — handled here as a
no-op, not reprocessed and not duplicated.

The outcome returned also drives the harness's retry/DLQ decision: RETRY
means back off and let Kafka redeliver; EXHAUSTED means the retry budget
(Job.attempts vs Job.max_attempts, both persisted — not an in-memory
counter that would reset on a crash) is spent and the harness should route
the message to the dead-letter topic and commit the offset to unblock the
partition.
"""

import datetime as dt
import logging
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Job, JobStatus, Result, WorkerExecution
from backend.models.enums import ExecutionStatus
from backend.repositories.job_repository import JobRepository
from backend.repositories.result_repository import ResultRepository
from backend.repositories.worker_execution_repository import WorkerExecutionRepository
from backend.workers.base import StageMessage, Worker

logger = logging.getLogger(__name__)


class ProcessingOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    ALREADY_DONE = "already_done"  # redelivery of a stage that already has a Result
    RETRY = "retry"  # failed, budget remains — harness should back off and not commit
    EXHAUSTED = "exhausted"  # failed, budget spent (or job missing) — harness should DLQ + commit


@dataclass
class ProcessingResult:
    outcome: ProcessingOutcome
    attempt: int = 0
    max_attempts: int = 0
    error: str | None = None


class StageProcessingService:
    def __init__(self, session: AsyncSession, worker: Worker) -> None:
        self._session = session
        self._worker = worker
        self._jobs = JobRepository(session)
        self._results = ResultRepository(session)
        self._executions = WorkerExecutionRepository(session)

    async def handle(self, message: StageMessage) -> ProcessingResult:
        existing = await self._results.get(message.job_id, message.stage)
        if existing is not None:
            logger.info(
                "job=%s stage=%s already has a result; redelivery, skipping",
                message.job_id,
                message.stage,
            )
            return ProcessingResult(outcome=ProcessingOutcome.ALREADY_DONE)

        job = await self._jobs.get(message.job_id)
        if job is None:
            # Nothing normal processing can do with this — route it to the
            # DLQ for a human to look at rather than either silently
            # dropping it or retrying forever against a job that will never
            # exist.
            logger.error("job %s not found", message.job_id)
            return ProcessingResult(
                outcome=ProcessingOutcome.EXHAUSTED, error=f"job {message.job_id} not found"
            )

        if job.status == JobStatus.PENDING:
            job.status = JobStatus.RUNNING
            job.started_at = dt.datetime.now(dt.UTC)

        attempt = job.attempts + 1

        try:
            result_payload = await self._worker.process(message)
        except Exception as exc:
            return await self._record_failure(job, message, attempt, exc)

        return await self._record_success(job, message, attempt, result_payload)

    async def _record_failure(
        self, job: Job, message: StageMessage, attempt: int, exc: Exception
    ) -> ProcessingResult:
        error_text = str(exc)[:2000]
        job.attempts = attempt
        job.last_error = error_text
        self._executions.add(
            WorkerExecution(
                job_id=job.id,
                worker_name=self._worker.name,
                stage=message.stage,
                attempt=attempt,
                status=ExecutionStatus.FAILED,
                error=error_text,
                finished_at=dt.datetime.now(dt.UTC),
            )
        )

        exhausted = attempt >= job.max_attempts
        if exhausted:
            job.status = JobStatus.DEAD_LETTERED
            # No dedicated "dead_lettered_at" column; completed_at implies
            # success, so leave it null and rely on the auto-maintained
            # updated_at for when this became terminal.

        await self._session.commit()
        return ProcessingResult(
            outcome=ProcessingOutcome.EXHAUSTED if exhausted else ProcessingOutcome.RETRY,
            attempt=attempt,
            max_attempts=job.max_attempts,
            error=error_text,
        )

    async def _record_success(
        self, job: Job, message: StageMessage, attempt: int, result_payload: dict
    ) -> ProcessingResult:
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

        return ProcessingResult(outcome=ProcessingOutcome.SUCCEEDED, attempt=attempt)
