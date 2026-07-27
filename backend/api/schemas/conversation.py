"""Request/response schemas for the conversation (messages) endpoints.

The planner response shape here is the long-term one from the architecture
doc, not a Phase-2-only format -- "proposal", not "plan", since later
phases add proposal approval and this way nothing gets renamed.
"""

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class PostMessageRequest(BaseModel):
    # Required, not optional: every user turn has a sender. Assistant/system
    # messages are the only ones with no sender_id, and those aren't posted
    # by clients.
    sender_id: uuid.UUID
    content: str


class PlannerMessage(BaseModel):
    type: Literal["message"]
    text: str


class PlannerProposal(BaseModel):
    type: Literal["proposal"]
    summary: str
    workflow: list[dict[str, Any]]


PlannerResponse = Annotated[PlannerMessage | PlannerProposal, Field(discriminator="type")]


class PostMessageResponse(BaseModel):
    message_id: uuid.UUID
    response: PlannerResponse


class MessageResponse(BaseModel):
    id: uuid.UUID
    role: str
    sender_id: uuid.UUID | None
    content: str
    created_at: dt.datetime


class ConversationHistoryResponse(BaseModel):
    messages: list[MessageResponse]
