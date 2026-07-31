"""Compiles a validated Proposal into Setu's native workflow/payload shape
(§6).

A pure function -- no DB, no storage, no API calls, no job submission, no
state mutation. Renamed from an earlier "translator" working name, since
its likely future responsibilities (asset resolution, param normalization,
default injection, shorthand expansion) are closer to compilation than
plain translation.

Assumes the proposal already passed validate_proposal -- this does not
re-check the registry.
"""

from dataclasses import dataclass
from typing import Any

from backend.services.proposal import Proposal
from backend.workers.media import PREVIEW_FLAG

__all__ = ["ExecutionContext", "UnknownVideoHandleError", "compile_workflow"]


class UnknownVideoHandleError(Exception):
    """Raised when a Proposal references a video handle not present in the
    ExecutionContext -- e.g. the project's videos changed between proposal
    and confirm (see build_video_contexts' ordering caveat), since
    validate_proposal's handle check runs at proposal time, not here."""

    def __init__(self, video_id: str) -> None:
        super().__init__(f"unknown video handle '{video_id}'")
        self.video_id = video_id


@dataclass(frozen=True)
class ExecutionContext:
    """Carries whatever a Proposal needs from outside itself to become a
    real job, kept separate so the proposal's own shape stays storage-
    agnostic. `video_uris` maps a ProposalStage's handle (e.g. "video_1",
    assigned by PlannerContext/PromptBuilder) to its resolved storage URI --
    a project can hold several videos, and different stages may reference
    different ones (Changelog v8). Still just this one field for V1 (only
    Video assets exist so far); grows without changing compile_workflow's
    signature."""

    video_uris: dict[str, str]

    # Compile the same proposal for a fast, low-resolution render instead
    # of the real one. Deliberately a mode of compilation rather than a
    # different pipeline: the result is an ordinary Job on the ordinary
    # topics, so it inherits retry, DLQ, idempotency, crash recovery and
    # tracing without any of that being reimplemented for previews.
    # Growing ExecutionContext is what its docstring above invites, and
    # keeps compile_workflow's signature stable.
    preview: bool = False


def compile_workflow(
    proposal: Proposal, context: ExecutionContext
) -> tuple[list[str], dict[str, Any]]:
    workflow = [item.stage for item in proposal.workflow]
    stage_params = {}
    for i, item in enumerate(proposal.workflow):
        video_uris = []
        for video_id in item.video_ids:
            if video_id not in context.video_uris:
                raise UnknownVideoHandleError(video_id)
            video_uris.append(context.video_uris[video_id])
        stage_params[str(i)] = {"params": item.params, "video_uris": video_uris}

    payload: dict[str, Any] = {"stage_params": stage_params}
    if context.preview:
        # Job-level rather than per-stage: it applies to the whole render,
        # and media.process_video reads it off the payload every capability
        # already receives. Imported from media rather than spelled again
        # here so the producer of this key and its only consumer cannot
        # drift -- a typo would silently disable preview, producing a
        # correct-but-slow full-quality render with nothing to indicate
        # anything went wrong.
        payload[PREVIEW_FLAG] = True
    return workflow, payload
