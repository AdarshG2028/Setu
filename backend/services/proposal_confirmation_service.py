"""ProposalConfirmationService (Changelog v8, Phase 4) -- the explicit
POST /projects/{id}/confirm-proposal entry point. Free-text chat
confirmation is deliberately not supported (item 10): a proposal is
confirmed by calling this endpoint, never inferred from a message's
content.

Obtains the latest proposal by scanning the conversation backwards for the
most recent {"type": "proposal"} message -- not just the last message,
since a clarifying turn can legitimately follow a proposal. No proposal
persistence (Phase 8/9 backlog item): the conversation transcript is the
only source of truth for Phase 4.

Idempotent via Setu's existing idempotency-key mechanism (Phase 1) rather
than a new persisted "already confirmed" flag: the key is derived from the
conversation id and the proposal message's own id, both already stable and
unique, so calling this twice replays the same Job instead of creating a
second one -- as long as compile_workflow's output stays a pure function of
the proposal + the project's video rows (no fresh timestamps, no
regenerated URIs), which it does.
"""

import json
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Message, MessageRole
from backend.repositories.conversation_repository import ConversationRepository
from backend.repositories.message_repository import MessageRepository
from backend.repositories.project_repository import ProjectNotFoundError, ProjectRepository
from backend.repositories.video_repository import VideoRepository
from backend.services.job_submission_service import JobSubmissionResult, JobSubmissionService
from backend.services.planner_context import build_video_contexts
from backend.services.proposal import Proposal
from backend.services.workflow_compiler import (
    ExecutionContext,
    UnknownVideoHandleError,
    compile_workflow,
)

__all__ = ["NoPendingProposalError", "ProposalConfirmationService", "UnknownVideoHandleError"]


class NoPendingProposalError(Exception):
    """Raised when the project's conversation has no `{"type": "proposal"}`
    message to confirm."""

    def __init__(self, project_id: uuid.UUID) -> None:
        super().__init__(f"project {project_id} has no pending proposal")
        self.project_id = project_id


class ProposalConfirmationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._projects = ProjectRepository(session)
        self._conversations = ConversationRepository(session)
        self._messages = MessageRepository(session)
        self._videos = VideoRepository(session)
        self._jobs = JobSubmissionService(session)

    async def confirm(self, project_id: uuid.UUID) -> JobSubmissionResult:
        """Execute the latest proposal for real."""
        return await self._submit(project_id, preview=False)

    async def preview(self, project_id: uuid.UUID) -> JobSubmissionResult:
        """Execute the latest proposal as a fast, low-resolution render.

        Same proposal, same capabilities, same execution path -- only the
        compilation mode differs. Kept as a sibling of confirm() over one
        shared body so the two provably cannot diverge in how they locate
        or compile the proposal; if they did, a preview would stop being
        evidence about what the real render will produce, which is its
        entire purpose.
        """
        return await self._submit(project_id, preview=True)

    async def _submit(
        self, project_id: uuid.UUID, *, preview: bool
    ) -> JobSubmissionResult:
        if await self._projects.get(project_id) is None:
            raise ProjectNotFoundError(project_id)

        conversation = await self._conversations.get_by_project(project_id)
        if conversation is None:
            raise NoPendingProposalError(project_id)

        history = await self._messages.list_by_conversation(conversation.id)
        proposal_message = self._latest_proposal_message(history)
        if proposal_message is None:
            raise NoPendingProposalError(project_id)

        proposal = Proposal.from_dict(json.loads(proposal_message.content))

        videos = await self._videos.list_by_project(project_id)
        video_uris = {
            video_context.handle: video.storage_uri
            for video_context, video in zip(build_video_contexts(videos), videos)
        }
        workflow, payload = compile_workflow(
            proposal, ExecutionContext(video_uris=video_uris, preview=preview)
        )

        # Separate namespaces, so previewing a proposal and then confirming
        # it produce two distinct jobs rather than the confirm replaying the
        # preview's low-resolution result -- which is exactly what a single
        # shared key would do, since everything else about the two calls is
        # identical by construction.
        prefix = "preview-proposal" if preview else "confirm-proposal"
        idempotency_key = f"{prefix}:{conversation.id}:{proposal_message.id}"
        return await self._jobs.submit(
            idempotency_key=idempotency_key, workflow=workflow, payload=payload
        )

    def _latest_proposal_message(self, history: list[Message]) -> Message | None:
        for message in reversed(history):
            if message.role != MessageRole.ASSISTANT:
                continue
            try:
                data = json.loads(message.content)
            except json.JSONDecodeError:
                continue
            if data.get("type") == "proposal":
                return message
        return None
