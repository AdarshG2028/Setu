"""Video — an uploaded source file. Metadata from Job #1 (video_analysis)
is not duplicated here; it's read back through latest_analysis_job_id, so
this row never needs a second write once analysis starts (see
VideoUploadService for why that ordering still leaves one narrow,
accepted crash window).
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.base import Base, TimestampMixin


class Video(Base, TimestampMixin):
    __tablename__ = "videos"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)

    # Opaque Storage URI (see backend/storage) — never parsed here.
    storage_uri: Mapped[str] = mapped_column(sa.Text, nullable=False)
    original_filename: Mapped[str] = mapped_column(sa.String(255), nullable=False)

    # Job #1 (workflow=["video_analysis"]) for this upload. Its status
    # tells callers whether analysis is still running, and once it's
    # completed, Result(job_id=this, stage=0).payload is the video's
    # metadata — no separate metadata column to keep in sync.
    latest_analysis_job_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (sa.Index("ix_videos_latest_analysis_job_id", "latest_analysis_job_id"),)
