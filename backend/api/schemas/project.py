"""Request/response schemas for the projects endpoint."""

import datetime as dt
import uuid

from pydantic import BaseModel


class CreateProjectRequest(BaseModel):
    owner_id: uuid.UUID
    name: str | None = None


class ProjectResponse(BaseModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str | None
    created_at: dt.datetime
