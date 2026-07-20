"""Job submission and lookup."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.schemas.job import JobCreateRequest, JobResponse
from backend.database.session import get_session
from backend.repositories.job_repository import JobRepository
from backend.services.job_submission_service import (
    IdempotencyConflictError,
    JobSubmissionService,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    body: JobCreateRequest,
    session: SessionDep,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1),
) -> JobResponse:
    service = JobSubmissionService(session)
    try:
        result = await service.submit(
            idempotency_key=idempotency_key,
            workflow=body.workflow,
            payload=body.payload,
        )
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    return JobResponse.from_model(result.job)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: uuid.UUID, session: SessionDep) -> JobResponse:
    job = await JobRepository(session).get(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return JobResponse.from_model(job)
