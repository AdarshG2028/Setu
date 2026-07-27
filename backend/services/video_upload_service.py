"""Video upload: store the bytes, create the Video row under its Project,
and submit Job #1 (video_analysis) — through JobSubmissionService,
completely unmodified.

Every video belongs to exactly one project (Video.project_id is NOT NULL,
see backend/models/video.py); the caller must create the project first.

Job #1's id isn't known until JobSubmissionService.submit() has already
committed (it assigns Job.id internally, mid-transaction, and this service
has no hook into that commit without changing submit() itself — which
stays untouched on purpose, see the architecture doc). So linking
Video.latest_analysis_job_id back to the job it created is necessarily a
second, separate commit right after. A crash in the narrow window between
the two commits leaves a Video row whose analysis job exists and will run
to completion, but isn't reachable from the video row yet — accepted for
V1, same as the other known gaps called out in the roadmap (e.g. Job
cancellation), rather than adding complexity to close it.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Job, Video
from backend.repositories.project_repository import ProjectNotFoundError, ProjectRepository
from backend.repositories.video_repository import VideoRepository
from backend.services.job_submission_service import JobSubmissionService
from backend.storage import get_storage


@dataclass
class VideoUploadResult:
    video: Video
    job: Job


class VideoUploadService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._projects = ProjectRepository(session)
        self._videos = VideoRepository(session)

    async def upload(
        self,
        *,
        project_id: uuid.UUID,
        data: bytes,
        filename: str,
        name: str | None = None,
    ) -> VideoUploadResult:
        if await self._projects.get(project_id) is None:
            raise ProjectNotFoundError(project_id)

        uri = get_storage().put(data, suggested_name=filename)

        video = Video(
            project_id=project_id, storage_uri=uri, original_filename=filename, name=name
        )
        self._videos.add(video)
        await self._session.flush()  # assigns video.id

        # A fresh, server-generated key: this call can never be a client
        # retry, so replay semantics don't apply here — it exists only to
        # satisfy JobSubmissionService's contract.
        submission = await JobSubmissionService(self._session).submit(
            idempotency_key=f"video-analysis:{video.id}",
            workflow=["video_analysis"],
            payload={"video_id": str(video.id), "video_uri": uri},
        )

        video.latest_analysis_job_id = submission.job.id
        await self._session.commit()

        return VideoUploadResult(video=video, job=submission.job)
