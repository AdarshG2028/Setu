"""Data access for Job. No business logic — that lives in services/."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Job


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, job: Job) -> None:
        self._session.add(job)

    async def get(self, job_id: uuid.UUID) -> Job | None:
        return await self._session.get(Job, job_id)
