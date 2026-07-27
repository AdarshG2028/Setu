"""Project — the top-level container conversations and videos belong to.

Introduced now, ahead of collaborative rooms (Phase 8), so Conversation can
be parented to it from the start instead of to Video: multi-member rooms
are just Project gaining more members later, not a re-parenting migration.
For Phase 2 every project has exactly one owner and one video.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.base import Base, TimestampMixin


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)

    # No users table yet (auth lands in Phase 8) -- plain UUID, no FK.
    owner_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    name: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
