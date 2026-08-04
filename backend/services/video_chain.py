"""Finds a video's most recent edited output, so a later proposal can build
on it instead of always compiling against the original upload.

The bug this exists to fix: a room asked to "trim 0:40-1:10", approved it,
then asked to "trim 0:10-0:15" -- and the second trim silently ran on the
untouched original, because nothing anywhere recorded what the first trim
produced. `Video.storage_uri` is set once at upload and never reassigned
(see backend/models/video.py); every proposal re-resolved handles straight
back to it.

**No new table.** `workflow_compiler.compile_workflow` already stamps
`payload["stage_params"]["0"]["video_ids"]` with the real `Video.id`
behind each handle it compiled (`ExecutionContext.video_db_ids`), and
`room_snapshot_service.export_artifacts` already knows how to pick a job's
real, non-preview, completed output apart from noise -- including
excluding previews, which must never advance what a later edit builds on
(a preview is deliberately fast and low-resolution; chaining onto one
would make every subsequent export degrade, silently). This module reuses
both rather than persisting a second copy of either.
"""

import datetime as dt
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.repositories.project_job_repository import ProjectJobRepository
from backend.repositories.result_repository import ResultRepository
from backend.services.room_snapshot_service import export_artifacts, final_result_by_job
from backend.workers.media import (
    extract_video_metadata,
    materialize_to_tempfile,
    primary_video,
    probe,
)

logger = logging.getLogger(__name__)

__all__ = ["VideoEdit", "measure", "recent_edits"]


@dataclass(frozen=True)
class VideoEdit:
    uri: str
    job_id: uuid.UUID
    edited_at: dt.datetime
    # The producing job's stage names, e.g. ["remove_segment", "trim"] --
    # what with_edit_history tells the planner is ALREADY reflected in this
    # handle, so an incremental request doesn't re-propose them on top of
    # an already-edited video and double-apply them (the bug this field
    # exists to let the prompt warn about: "remove the last 5 secs of this
    # too" re-including remove_segment cut a second, different 30s out of
    # an already-cut clip instead of just trimming the extra 5s).
    stages: list[str]


async def recent_edits(
    session: AsyncSession, project_id: uuid.UUID, video_id: uuid.UUID, *, limit: int = 2
) -> list[VideoEdit]:
    """This video's most recent completed, non-preview, single-video edits,
    newest first, capped at `limit`. [] if it has never been through one.

    A bounded window, not a full version history: the room can revert to
    the latest edit, the one before it, or the original -- not to an
    arbitrary point further back. That is a product choice (asked for
    exactly as "root and last two"), not a technical ceiling; raising
    `limit` returns a longer window without any other change here.

    **"Single-video" is deliberate.** A job only counts as one of this
    video's versions when stage 0 named exactly this one video and nothing
    else -- a merge, which names two, produces no single video's "next
    version", and this must never guess which of the two it was.
    Restricting to stage 0 is safe: it is the only stage a job's video_ids
    are ever load-bearing for (every later stage takes its real input from
    the prior stage's output, see backend/workers/media.py
    resolve_input_uri), so stage 0 is where "what did this job start from"
    is answered.

    `list_completed_jobs` is already ordered newest-completed-first (the
    same ordering the room's version list depends on), so collecting
    matches in that order and stopping at `limit` is already newest-first.
    """
    completed = await ProjectJobRepository(session).list_completed_jobs(project_id)
    if not completed:
        return []

    results_by_job = final_result_by_job(
        await ResultRepository(session).list_by_jobs([job.id for job in completed])
    )

    target = str(video_id)
    found: list[VideoEdit] = []
    for job in completed:
        if len(found) >= limit:
            break
        stage_zero = (job.payload or {}).get("stage_params", {}).get("0", {})
        if stage_zero.get("video_ids") != [target]:
            continue
        # export_artifacts is [] for a preview, an unfinished job, or one
        # whose final stage produced nothing chainable (transcribe/
        # detect_scenes alone) -- exactly the cases that must not count.
        video_asset = primary_video(export_artifacts(job, results_by_job.get(job.id)))
        if video_asset is not None:
            found.append(
                VideoEdit(
                    uri=video_asset.uri,
                    job_id=job.id,
                    edited_at=job.completed_at or job.created_at,
                    stages=list(job.workflow.get("workflow", [])),
                )
            )
    return found


async def measure(uri: str) -> dict[str, Any] | None:
    """duration/resolution/orientation/fps for a stored video, measured
    fresh via ffprobe right now -- None if that fails for any reason.

    Why this exists: `VideoContext.duration_seconds` otherwise only ever
    carries the ORIGINAL upload's upload-time analysis, which is wrong for
    an edited handle -- a room that trims a clip and is then asked "how
    long is it now" had nothing but a stale number and a warning that it
    "may not be accurate," which is exactly what pushed a real
    conversation into asking the room for the duration in chat, getting a
    correct answer, misreading which duration it was an answer to, and
    then hallucinating one entirely on the turn after that. Grounding the
    edited handle's facts in a real measurement removes the need to guess.

    Broad except is deliberate: this only ever enriches a chat turn or a
    proposal compile with better facts, so a probe failure (missing file,
    ffprobe crashing, an unreachable storage backend) must degrade to "no
    fresher facts than before," never break the turn that asked for them
    -- the same reasoning backend/services/job_progress_poller.py uses for
    swallowing a failed poll rather than taking the whole room down over it.
    """
    try:
        with materialize_to_tempfile(uri) as path:
            probe_data = await probe(path)
        return extract_video_metadata(probe_data)
    except Exception:
        logger.warning("could not measure video for chat context", extra={"uri": uri}, exc_info=True)
        return None
