"""PlannerContext (Changelog v8) -- everything a planning call needs,
assembled once by ConversationService before invoking the planner. Replaces
passing "many unrelated arguments" into Planner.respond; the planner never
fetches data itself (project/conversation/videos/preferences all come in
already loaded).
"""

import uuid
from dataclasses import dataclass, field

from backend.models import Message, Project, UserPreference, Video
from backend.services.capability_registry import CapabilityRegistry


@dataclass(frozen=True)
class VideoContext:
    """One of the project's videos as the planner sees it: a short,
    LLM-facing handle (never a raw Video.id -- models are unreliable at
    echoing UUIDs verbatim) plus enough to describe it in a prompt."""

    handle: str
    video_id: str
    display_name: str

    # Measured by the video_analysis worker on upload (§8) and stored on
    # its Result. Optional because analysis may still be running, or may
    # have failed, and a planner that can still propose *something* is
    # better than one that refuses.
    #
    # Without these the planner is blind to facts the system already
    # knows: asked to "trim the last 10 seconds" it has to ask how long
    # the video is, and asked to "make it vertical" it cannot tell that it
    # already is.
    duration_seconds: float | None = None
    resolution: str | None = None
    orientation: str | None = None


def build_video_contexts(
    videos: list[Video], analysis: dict[str, dict] | None = None
) -> list[VideoContext]:
    """Assigns stable handles ("video_1", "video_2", ...) in the given
    order. Callers must pass videos in a consistent order (VideoRepository
    .list_by_project already orders by created_at) -- confirm-proposal
    re-derives the same handle -> uri mapping later by calling this again,
    so a proposal's video_ids only resolve correctly if the project's video
    list hasn't changed shape in between. Acceptable for V1 per the
    "no proposal persistence in Phase 4" deferral; revisit if that
    assumption ever breaks in practice.
    """
    analysis = analysis or {}
    return [
        VideoContext(
            handle=f"video_{i}",
            video_id=str(video.id),
            display_name=video.name or video.original_filename,
            duration_seconds=analysis.get(str(video.id), {}).get("duration_seconds"),
            resolution=analysis.get(str(video.id), {}).get("resolution"),
            orientation=analysis.get(str(video.id), {}).get("orientation"),
        )
        for i, video in enumerate(videos, start=1)
    ]


@dataclass(frozen=True)
class PlannerContext:
    project: Project
    conversation_history: list[Message]
    videos: list[VideoContext]
    preferences: UserPreference | None
    capability_registry: CapabilityRegistry
    # The room's approval policy, rendered so the planner can *describe*
    # what happens next ("waiting for approval from all team members").
    # It never decides outcomes -- that is the collaboration layer's job --
    # so this is wording only (Phase 9a).
    approval_policy: str | None = field(default=None)


def participant_handles(history: list[Message]) -> dict[uuid.UUID, str]:
    """Stable, short labels for the humans in a conversation (Phase 9a).

    There is no users table -- a sender is a bare asserted UUID -- so the
    planner has no names to work with, and a raw UUID is both unreadable
    and something models echo unreliably. The same reasoning produced
    `video_1` handles in Phase 4, and this follows it: numbered in order of
    first appearance, so a given member keeps one label for the whole
    transcript and "member_2 disagreed with member_1" is a statement the
    model can actually make.

    Assistant turns are excluded: they have no sender, and the planner
    knows which turns are its own from the chat role.
    """
    handles: dict[uuid.UUID, str] = {}
    for message in history:
        if message.sender_id is None or message.sender_id in handles:
            continue
        handles[message.sender_id] = f"member_{len(handles) + 1}"
    return handles
