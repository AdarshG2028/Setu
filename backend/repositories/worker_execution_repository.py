"""Data access for WorkerExecution — the per-attempt audit trail."""

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WorkerExecution


class WorkerExecutionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, execution: WorkerExecution) -> None:
        self._session.add(execution)
