"""Request/response schemas for the videos endpoints."""

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel


class VideoUploadResponse(BaseModel):
    video_id: uuid.UUID
    job_id: uuid.UUID
    status: str


class VideoDetailResponse(BaseModel):
    id: uuid.UUID
    original_filename: str
    # "uploaded" (no analysis job yet — shouldn't normally be observed,
    # upload always submits one) | "analyzing" | "analyzed" | "failed"
    status: str
    analysis: dict[str, Any] | None
    created_at: dt.datetime
