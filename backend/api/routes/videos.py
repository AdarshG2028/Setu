"""Video upload and lookup — Job #1 (video_analysis) end to end."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.schemas.video import VideoDetailResponse, VideoUploadResponse
from backend.database.session import get_session
from backend.models import JobStatus
from backend.repositories.job_repository import JobRepository
from backend.repositories.result_repository import ResultRepository
from backend.repositories.video_repository import VideoRepository
from backend.services.video_upload_service import VideoUploadService

router = APIRouter(prefix="/videos", tags=["videos"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# video_analysis is always stage 0 of Job #1 — a single-stage workflow.
_ANALYSIS_STAGE = 0
_TERMINAL_FAILURE_STATUSES = {JobStatus.FAILED, JobStatus.DEAD_LETTERED, JobStatus.CANCELLED}


@router.post("", response_model=VideoUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_video(
    session: SessionDep, file: Annotated[UploadFile, File()]
) -> VideoUploadResponse:
    data = await file.read()
    result = await VideoUploadService(session).upload(
        data=data, filename=file.filename or "upload"
    )
    return VideoUploadResponse(video_id=result.video.id, job_id=result.job.id, status="analyzing")


@router.get("/{video_id}", response_model=VideoDetailResponse)
async def get_video(video_id: uuid.UUID, session: SessionDep) -> VideoDetailResponse:
    video = await VideoRepository(session).get(video_id)
    if video is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="video not found")

    analysis_status = "uploaded"
    analysis = None
    if video.latest_analysis_job_id is not None:
        job = await JobRepository(session).get(video.latest_analysis_job_id)
        if job is not None:
            if job.status == JobStatus.COMPLETED:
                analysis_status = "analyzed"
                result = await ResultRepository(session).get(job.id, _ANALYSIS_STAGE)
                analysis = result.payload if result else None
            elif job.status in _TERMINAL_FAILURE_STATUSES:
                analysis_status = "failed"
            else:
                analysis_status = "analyzing"

    return VideoDetailResponse(
        id=video.id,
        original_filename=video.original_filename,
        status=analysis_status,
        analysis=analysis,
        created_at=video.created_at,
    )
