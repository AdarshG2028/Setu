"""ConversationService — the single orchestration layer for a chat turn.

Routes never touch repositories or the planner directly; this is the one
place that sequences load/create-conversation -> load-preferences ->
append-user-message -> Planner.respond -> append-assistant-message ->
return. Phase 4 swaps StaticPlanner for a real LLMPlanner by passing a
different Planner in here -- nothing about this sequencing changes.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_settings
from backend.models import Conversation, Message, MessageRole
from backend.repositories.conversation_repository import ConversationRepository
from backend.repositories.message_repository import MessageRepository
from backend.repositories.project_repository import ProjectNotFoundError, ProjectRepository
from backend.repositories.user_preference_repository import UserPreferenceRepository
from backend.services.planner import Planner, StaticPlanner

__all__ = ["ConversationService", "PostMessageResult", "ProjectNotFoundError"]


@dataclass
class PostMessageResult:
    message_id: uuid.UUID
    response: dict[str, Any]


class ConversationService:
    def __init__(self, session: AsyncSession, *, planner: Planner | None = None) -> None:
        self._session = session
        self._projects = ProjectRepository(session)
        self._conversations = ConversationRepository(session)
        self._messages = MessageRepository(session)
        self._preferences = UserPreferenceRepository(session)
        self._planner = planner or StaticPlanner()

    async def post_message(
        self, *, project_id: uuid.UUID, sender_id: uuid.UUID, content: str
    ) -> PostMessageResult:
        if await self._projects.get(project_id) is None:
            raise ProjectNotFoundError(project_id)

        conversation = await self._get_or_create_conversation(project_id)

        # Loaded even though every row is empty until Phase 6 writes one --
        # keeps this sequence stable; only the preference *writer* changes
        # later, not this service.
        preferences = await self._preferences.get(sender_id)

        user_message = Message(
            conversation_id=conversation.id,
            sender_id=sender_id,
            role=MessageRole.USER,
            content=content,
        )
        self._messages.add(user_message)
        await self._session.flush()  # assigns id + created_at before the planner sees it

        history = await self._messages.list_by_conversation(
            conversation.id, limit=get_settings().conversation_context_limit
        )
        response = await self._planner.respond(history, preferences)

        assistant_message = Message(
            conversation_id=conversation.id,
            sender_id=None,
            role=MessageRole.ASSISTANT,
            content=json.dumps(response),
        )
        self._messages.add(assistant_message)
        await self._session.commit()

        return PostMessageResult(message_id=user_message.id, response=response)

    async def get_history(self, project_id: uuid.UUID) -> list[Message]:
        if await self._projects.get(project_id) is None:
            raise ProjectNotFoundError(project_id)

        conversation = await self._conversations.get_by_project(project_id)
        if conversation is None:
            return []
        # Unbounded: this is a user reading the transcript, not the planner's
        # bounded context window -- those are deliberately different limits.
        return await self._messages.list_by_conversation(conversation.id)

    async def _get_or_create_conversation(self, project_id: uuid.UUID) -> Conversation:
        conversation = await self._conversations.get_by_project(project_id)
        if conversation is not None:
            return conversation
        conversation = Conversation(project_id=project_id)
        self._conversations.add(conversation)
        await self._session.flush()
        return conversation
