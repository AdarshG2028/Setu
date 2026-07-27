"""Domain models for a proposal (§6) -- the planner's structured output,
before it's validated and compiled into a Setu job.

Frozen dataclasses, matching StageMessage's existing convention
(backend/workers/base.py) -- Pydantic stays this repo's API-schema layer
(backend/api/schemas/), not its internal-domain layer.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProposalStage:
    stage: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Proposal:
    summary: str
    workflow: list[ProposalStage]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Proposal":
        """Parses the raw `{"summary": ..., "workflow": [...]}` shape (§6)
        -- the external representation a proposal arrives in, whether
        hand-authored (Phase 3) or LLM-generated (Phase 4). Raises KeyError
        if a workflow item is missing "stage": that's a structurally
        malformed proposal, a different failure mode from the semantic
        checks validate_proposal performs against the capability registry.
        """
        workflow = [
            ProposalStage(stage=item["stage"], params=item.get("params", {}))
            for item in data.get("workflow", [])
        ]
        return cls(summary=data.get("summary", ""), workflow=workflow)
