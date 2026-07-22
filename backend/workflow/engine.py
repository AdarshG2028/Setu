"""Workflow engine: sequential dispatch only, no branching/DAGs yet.

Owns *workflow position* (where a job is in its `workflow` list) — never
*job lifecycle* (Job.status). It never imports anything from
backend/workers/: adding a worker is a new Worker subclass plus one line
in backend/workers/cli.py's WORKERS registry, nothing here changes.

Dispatch reuses the outbox pattern rather than producing to Kafka
directly: the next stage's OutboxEvent is added to the same session
StageProcessingService.handle() is already about to commit, so it
inherits that transaction's atomicity for free — a stage's Result and its
successor's dispatch either both land or neither does. See
stage_processing_service.py for how this is wired in, and journal.md for
why (a direct-produce or a separate polling engine would each reintroduce
a "dual write" or a second delivery mechanism with its own crash/retry
story to solve from scratch).

Also injects the current OpenTelemetry span context into the dispatched
OutboxEvent's trace_context, the same mechanism
job_submission_service.py uses for the first stage (see that file and
outbox_publisher.py, which already extracts trace_context generically
for any event, not just job-submission ones). Without this, a
multi-stage job's trace would break at every stage boundary instead of
staying one connected trace end to end -- this runs inside runner.py's
"stage.process" span, so the context captured here is that span's,
continuing the same trace into the next stage's dispatch and processing.
"""

from dataclasses import dataclass

from opentelemetry import propagate

from backend.models import Job, OutboxEvent
from backend.repositories.outbox_repository import OutboxRepository
from backend.workers.base import StageMessage


@dataclass(frozen=True)
class WorkflowProgress:
    stage: int  # stages completed so far (== job.current_stage after advance())
    total_stages: int
    is_last_stage: bool

    @property
    def label(self) -> str:
        return f"Stage {self.stage}/{self.total_stages}"


class WorkflowEngine:
    def __init__(self, outbox: OutboxRepository) -> None:
        self._outbox = outbox

    def advance(self, job: Job, message: StageMessage) -> WorkflowProgress:
        """Called after a stage succeeds. Always updates job.current_stage;
        dispatches the next stage's OutboxEvent only if this wasn't the
        last one. Never touches job.status — that's the caller's call to
        make, not a workflow-position concern.
        """
        total_stages = len(message.workflow)
        job.current_stage = message.stage + 1
        is_last_stage = job.current_stage >= total_stages

        if not is_last_stage:
            self._dispatch_next_stage(job, message)

        return WorkflowProgress(
            stage=job.current_stage,
            total_stages=total_stages,
            is_last_stage=is_last_stage,
        )

    def _dispatch_next_stage(self, job: Job, message: StageMessage) -> None:
        next_stage = job.current_stage
        next_topic = message.workflow[next_stage]

        trace_context: dict[str, str] = {}
        propagate.inject(trace_context)

        self._outbox.add(
            OutboxEvent(
                aggregate_type="job",
                aggregate_id=job.id,
                event_type="job.stage_advanced",
                topic=next_topic,
                partition_key=str(job.id),
                payload={
                    "job_id": str(job.id),
                    "stage": next_stage,
                    "workflow": message.workflow,
                    "payload": message.payload,
                },
                trace_context=trace_context,
            )
        )
