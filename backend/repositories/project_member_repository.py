"""Data access for ProjectMember. No business logic — that lives in services/."""

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import ProjectMember

OWNER = "owner"
MEMBER = "member"


class ProjectMemberRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def is_member(self, project_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """The membership guard's one question, kept as a single indexed
        primary-key lookup rather than loading the row."""
        result = await self._session.execute(
            select(ProjectMember.role).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def get_role(self, project_id: uuid.UUID, user_id: uuid.UUID) -> str | None:
        result = await self._session.execute(
            select(ProjectMember.role).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_project(self, project_id: uuid.UUID) -> list[ProjectMember]:
        result = await self._session.execute(
            select(ProjectMember)
            .where(ProjectMember.project_id == project_id)
            .order_by(ProjectMember.joined_at)
        )
        return list(result.scalars().all())

    async def list_project_ids_for(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        """Which rooms this user is in — served by ix_project_members_user_id."""
        result = await self._session.execute(
            select(ProjectMember.project_id).where(ProjectMember.user_id == user_id)
        )
        return list(result.scalars().all())

    async def add(
        self, project_id: uuid.UUID, user_id: uuid.UUID, *, role: str = MEMBER
    ) -> None:
        """Idempotent by design.

        Inviting someone already in the room, or a client retrying a join,
        is a no-op rather than an IntegrityError -- and crucially does not
        demote an existing owner to member, which a plain upsert of `role`
        would.
        """
        await self._session.execute(
            insert(ProjectMember)
            .values(project_id=project_id, user_id=user_id, role=role)
            .on_conflict_do_nothing(index_elements=["project_id", "user_id"])
        )
