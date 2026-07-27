"""Data access for UserPreference. No business logic — that lives in services/.

No writer exists yet (Phase 6 adds it) -- only the read path is wired here.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import UserPreference


class UserPreferenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: uuid.UUID) -> UserPreference | None:
        return await self._session.get(UserPreference, user_id)
