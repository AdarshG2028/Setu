"""Data access for ProjectJob. No business logic — that lives in services/."""

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Job, ProjectJob


class ProjectJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self, *, project_id: uuid.UUID, job_id: uuid.UUID, submitted_by_user_id: uuid.UUID
    ) -> None:
        """Idempotent: a replayed submission (Setu's idempotency key returns
        the original job) must not fail here, and must not rewrite who
        owns it -- the first submitter stays the owner."""
        await self._session.execute(
            insert(ProjectJob)
            .values(
                project_id=project_id,
                job_id=job_id,
                submitted_by_user_id=submitted_by_user_id,
            )
            .on_conflict_do_nothing(index_elements=["job_id"])
        )

    async def project_for_job(self, job_id: uuid.UUID) -> uuid.UUID | None:
        """Which room this job belongs to — a primary-key lookup, since it
        is asked on every artifact fetch."""
        result = await self._session.execute(
            select(ProjectJob.project_id).where(ProjectJob.job_id == job_id)
        )
        return result.scalar_one_or_none()

    async def owner_of_job(self, job_id: uuid.UUID) -> uuid.UUID | None:
        """Who set this job running. Phase 9b authorizes cancellation
        against exactly this."""
        result = await self._session.execute(
            select(ProjectJob.submitted_by_user_id).where(ProjectJob.job_id == job_id)
        )
        return result.scalar_one_or_none()

    async def list_jobs_for_project(
        self, project_id: uuid.UUID, *, limit: int = 50
    ) -> list[Job]:
        """The room's jobs, most recent first — what the snapshot endpoint
        and the progress poller both need."""
        result = await self._session.execute(
            select(Job)
            .join(ProjectJob, ProjectJob.job_id == Job.id)
            .where(ProjectJob.project_id == project_id)
            .order_by(Job.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
