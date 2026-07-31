"""Data access for Result."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Result


class ResultRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, job_id: uuid.UUID, stage: int) -> Result | None:
        """Presence of this row is the idempotency check for a stage."""
        result = await self._session.execute(
            select(Result).where(Result.job_id == job_id, Result.stage == stage)
        )
        return result.scalar_one_or_none()

    async def list_by_job(self, job_id: uuid.UUID) -> list[Result]:
        """Every stage's result, in execution order — the artifact listing
        reads this to show what each stage produced, not just the final
        output."""
        result = await self._session.execute(
            select(Result).where(Result.job_id == job_id).order_by(Result.stage)
        )
        return list(result.scalars().all())

    def add(self, result: Result) -> None:
        self._session.add(result)
