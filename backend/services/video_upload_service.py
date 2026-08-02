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
from backend.repositories.project_job_repository import ProjectJobRepository
from backend.repositories.project_repository import ProjectNotFoundError, ProjectRepository
from backend.repositories.video_repository import DuplicateVideoNameError, VideoRepository
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
        # Required, not optional: every video belongs to exactly one
        # project, and an unmapped analysis job is a job the artifact
        # guard cannot authorize. There is no caller without a room.
        uploaded_by: uuid.UUID,
    ) -> VideoUploadResult:
        if await self._projects.get(project_id) is None:
            raise ProjectNotFoundError(project_id)

        # Checked before storage.put(): a name collision means this
        # upload is rejected outright, so there is no point spending a
        # write on bytes that won't be kept. Two videos sharing a name
        # leaves the planner's video_1/video_2 handles as the only way to
        # tell them apart, which defeats the point of naming them at all
        # (observed live: a clarifying question that showed the same name
        # for both candidates). Only checked when a name was actually
        # given -- unnamed uploads fall back to original_filename at the
        # display layer, not here, so this must not reject on None.
        if name is not None and await self._videos.get_by_project_and_name(project_id, name) is not None:
            raise DuplicateVideoNameError(project_id, name)

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
        # The analysis job belongs to the room as well, so it shows up in
        # the room snapshot and its progress can be broadcast. Rides the
        # same follow-up commit as latest_analysis_job_id -- submit()
        # already committed the job itself, so both of these are a second
        # transaction either way.
        await ProjectJobRepository(self._session).add(
            project_id=project_id,
            job_id=submission.job.id,
            submitted_by_user_id=uploaded_by,
        )
        await self._session.commit()

        return VideoUploadResult(video=video, job=submission.job)
