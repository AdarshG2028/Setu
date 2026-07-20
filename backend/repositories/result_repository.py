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

    def add(self, result: Result) -> None:
        self._session.add(result)
