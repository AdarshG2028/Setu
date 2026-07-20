"""OutboxEvent — the transactional outbox that guarantees delivery to Kafka.

Written in the same transaction as the state change it describes. The publisher
polls unpublished rows and only marks them published after Kafka acks.
"""

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.base import Base, JSONType, TimestampMixin
from backend.models.enums import OutboxStatus


class OutboxEvent(Base, TimestampMixin):
    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, primary_key=True, default=uuid.uuid4
    )

    aggregate_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)

    event_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    topic: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    # Kafka partition key — same aggregate lands on the same partition, so
    # per-job event ordering is preserved.
    partition_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)

    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)

    status: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default=OutboxStatus.PENDING
    )
    published_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('pending','published','failed')",
            name="ck_outbox_events_status",
        ),
        # Partial index: the publisher's hot path is "unpublished, oldest first",
        # and it stays small even as published rows accumulate.
        sa.Index(
            "ix_outbox_events_unpublished",
            "created_at",
            postgresql_where=sa.text("published_at IS NULL"),
        ),
        sa.Index("ix_outbox_events_aggregate", "aggregate_type", "aggregate_id"),
    )
