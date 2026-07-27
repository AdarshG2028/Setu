"""Conversation — the shared message thread for a Project.

Belongs to Project, not Video: even in Phase 2's one-video-per-project
world, this keeps the same shape Phase 8's multi-video, multi-member rooms
need, so this table is never re-parented later.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.base import Base, TimestampMixin


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)

    project_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )

    __table_args__ = (
        # One shared conversation per project -- ConversationService gets-or-creates
        # keyed by project_id, never a list of conversations per project.
        sa.UniqueConstraint("project_id", name="uq_conversations_project_id"),
    )
