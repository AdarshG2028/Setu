"""Request/response schemas for the projects endpoint."""

import datetime as dt
import uuid

from pydantic import BaseModel


class CreateProjectRequest(BaseModel):
    # owner_id used to live here. It now comes from the X-User-Id header
    # (backend/api/deps.py): a body field cannot be read by a dependency,
    # so a membership guard could never have checked it.
    name: str | None = None


class ProjectResponse(BaseModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str | None
    created_at: dt.datetime
