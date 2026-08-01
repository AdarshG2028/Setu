"""Project creation, the project-scoped chat loop, and video upload.

Messages and videos are both routed under /projects, not as flat top-level
resources: Conversation and Video both belong to Project (see
backend/models/conversation.py, backend/models/video.py) -- Project is the
aggregate root, and everything hangs off it here for that reason. No
endpoint gets moved or renamed once Phase 8 adds multi-member rooms and
Phase 9 adds proposal approval.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.schemas.conversation import (
    ConfirmProposalResponse,
    ConversationHistoryResponse,
    MessageResponse,
    PostMessageRequest,
    PostMessageResponse,
)
from backend.api.schemas.project import CreateProjectRequest, ProjectResponse
from backend.api.schemas.video import VideoListResponse, VideoSummaryResponse, VideoUploadResponse
from backend.api.deps import CurrentUserDep, ProjectMemberDep, SessionDep
from backend.models import Project
from backend.repositories.project_member_repository import OWNER, ProjectMemberRepository
from backend.repositories.project_repository import ProjectNotFoundError, ProjectRepository
from backend.repositories.video_repository import VideoRepository
from backend.services.conversation_service import ConversationService
from backend.services.planner_factory import get_default_planner
from backend.services.proposal_confirmation_service import (
    NoPendingProposalError,
    ProposalConfirmationService,
    UnknownVideoHandleError,
)
from backend.services.video_upload_service import VideoUploadService

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    request: CreateProjectRequest, session: SessionDep, user_id: CurrentUserDep
) -> ProjectResponse:
    project = Project(owner_id=user_id, name=request.name)
    ProjectRepository(session).add(project)
    await session.flush()  # assigns project.id before the membership row
    # In the same transaction as the project itself: a project that exists
    # but has no members would be unreachable through the membership
    # guard, including by the person who just created it.
    await ProjectMemberRepository(session).add(project.id, user_id, role=OWNER)
    await session.commit()
    return ProjectResponse(
        id=project.id,
        owner_id=project.owner_id,
        name=project.name,
        created_at=project.created_at,
    )


@router.post("/{project_id}/messages", response_model=PostMessageResponse)
async def post_message(
    project_id: uuid.UUID,
    request: PostMessageRequest,
    session: SessionDep,
    user_id: ProjectMemberDep,
) -> PostMessageResponse:
    try:
        result = await ConversationService(
            session, planner=get_default_planner()
        ).post_message(
            project_id=project_id, sender_id=user_id, content=request.content
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project not found"
        ) from exc
    return PostMessageResponse(message_id=result.message_id, response=result.response)


@router.get("/{project_id}/messages", response_model=ConversationHistoryResponse)
async def get_messages(
    project_id: uuid.UUID, session: SessionDep, user_id: ProjectMemberDep
) -> ConversationHistoryResponse:
    try:
        messages = await ConversationService(session).get_history(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project not found"
        ) from exc
    return ConversationHistoryResponse(
        messages=[
            MessageResponse(
                id=m.id,
                role=m.role,
                sender_id=m.sender_id,
                content=m.content,
                created_at=m.created_at,
            )
            for m in messages
        ]
    )


@router.post(
    "/{project_id}/videos", response_model=VideoUploadResponse, status_code=status.HTTP_201_CREATED
)
async def upload_video(
    project_id: uuid.UUID,
    session: SessionDep,
    user_id: ProjectMemberDep,
    file: Annotated[UploadFile, File()],
    name: Annotated[str | None, Form()] = None,
) -> VideoUploadResponse:
    data = await file.read()
    try:
        result = await VideoUploadService(session).upload(
            project_id=project_id, data=data, filename=file.filename or "upload", name=name
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project not found"
        ) from exc
    return VideoUploadResponse(
        video_id=result.video.id,
        project_id=project_id,
        job_id=result.job.id,
        name=result.video.name,
        status="analyzing",
    )


@router.post("/{project_id}/confirm-proposal", response_model=ConfirmProposalResponse)
async def confirm_proposal(
    project_id: uuid.UUID, session: SessionDep, user_id: ProjectMemberDep
) -> ConfirmProposalResponse:
    try:
        result = await ProposalConfirmationService(session).confirm(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project not found"
        ) from exc
    except NoPendingProposalError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="no pending proposal to confirm"
        ) from exc
    except UnknownVideoHandleError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"proposal references unknown video handle '{exc.video_id}'",
        ) from exc
    return ConfirmProposalResponse(job_id=result.job.id, replayed=result.replayed)


@router.post("/{project_id}/preview-proposal", response_model=ConfirmProposalResponse)
async def preview_proposal(
    project_id: uuid.UUID, session: SessionDep, user_id: ProjectMemberDep
) -> ConfirmProposalResponse:
    """Render the latest proposal fast and low-resolution, for review.

    Deliberately a sibling of confirm-proposal rather than a flag on it:
    previewing is a different intent from committing, and Phase 9a will
    gate the two differently -- a preview is cheap and reversible, a
    confirm is what the room's approval policy governs.

    Returns the same shape as confirm-proposal: a preview is an ordinary
    Job, polled and downloaded through exactly the same endpoints.
    """
    try:
        result = await ProposalConfirmationService(session).preview(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project not found"
        ) from exc
    except NoPendingProposalError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="no pending proposal to preview"
        ) from exc
    except UnknownVideoHandleError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"proposal references unknown video handle '{exc.video_id}'",
        ) from exc
    return ConfirmProposalResponse(job_id=result.job.id, replayed=result.replayed)


@router.get("/{project_id}/videos", response_model=VideoListResponse)
async def list_videos(
    project_id: uuid.UUID, session: SessionDep, user_id: ProjectMemberDep
) -> VideoListResponse:
    if await ProjectRepository(session).get(project_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    videos = await VideoRepository(session).list_by_project(project_id)
    return VideoListResponse(
        videos=[
            VideoSummaryResponse(
                id=v.id,
                original_filename=v.original_filename,
                name=v.name,
                created_at=v.created_at,
            )
            for v in videos
        ]
    )
