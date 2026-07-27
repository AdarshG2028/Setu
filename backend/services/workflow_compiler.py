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
    agnostic. One field for V1 (this project only has Video assets so
    far); grows without changing compile_workflow's signature."""

    video_uri: str


def compile_workflow(
    proposal: Proposal, context: ExecutionContext
) -> tuple[list[str], dict[str, Any]]:
    workflow = [item.stage for item in proposal.workflow]
    stage_params = {str(i): item.params for i, item in enumerate(proposal.workflow)}
    payload = {"video_uri": context.video_uri, "stage_params": stage_params}
    return workflow, payload
