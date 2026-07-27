"""PlannerContext (Changelog v8) -- everything a planning call needs,
assembled once by ConversationService before invoking the planner. Replaces
passing "many unrelated arguments" into Planner.respond; the planner never
fetches data itself (project/conversation/videos/preferences all come in
already loaded).
"""

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


def build_video_contexts(videos: list[Video]) -> list[VideoContext]:
    """Assigns stable handles ("video_1", "video_2", ...) in the given
    order. Callers must pass videos in a consistent order (VideoRepository
    .list_by_project already orders by created_at) -- confirm-proposal
    re-derives the same handle -> uri mapping later by calling this again,
    so a proposal's video_ids only resolve correctly if the project's video
    list hasn't changed shape in between. Acceptable for V1 per the
    "no proposal persistence in Phase 4" deferral; revisit if that
    assumption ever breaks in practice.
    """
    return [
        VideoContext(
            handle=f"video_{i}",
            video_id=str(video.id),
            display_name=video.name or video.original_filename,
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
    # Future-compatible placeholder for Phase 9a (multi-participant approval
    # policies). Unused in Phase 4 -- present now so that phase doesn't need
    # to change this shape again.
    approval_policy: str | None = field(default=None)
