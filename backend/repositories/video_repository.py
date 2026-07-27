"""Data access for Video. No business logic — that lives in services/."""

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Video


class VideoRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, video: Video) -> None:
        self._session.add(video)

    async def get(self, video_id: uuid.UUID) -> Video | None:
        return await self._session.get(Video, video_id)

    async def list_by_project(self, project_id: uuid.UUID) -> list[Video]:
        result = await self._session.execute(
            sa.select(Video)
            .where(Video.project_id == project_id)
            .order_by(Video.created_at)
        )
        return list(result.scalars().all())
