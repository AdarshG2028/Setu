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


def compile_workflow(
    proposal: Proposal, context: ExecutionContext
) -> tuple[list[str], dict[str, Any]]:
    workflow = [item.stage for item in proposal.workflow]
    stage_params = {
        str(i): {
            "params": item.params,
            "video_uris": [context.video_uris[video_id] for video_id in item.video_ids],
        }
        for i, item in enumerate(proposal.workflow)
    }
    payload = {"stage_params": stage_params}
    return workflow, payload
