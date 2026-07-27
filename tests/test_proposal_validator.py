"""Unit tests for validate_proposal (§6).

Uses a small fake registry, not DEFAULT_CAPABILITY_REGISTRY -- validate_proposal
takes the registry as a parameter specifically so these tests don't couple to
whatever's really registered in production (which grows every sub-phase of
Phase 5).
"""

from backend.services.capability_registry import CapabilityRegistry, StageCapability
from backend.services.proposal import Proposal, ProposalStage
from backend.services.proposal_validator import validate_proposal

_REGISTRY = CapabilityRegistry(
    {
        "crop": StageCapability(
            name="crop", description="crop", parameter_schema={"aspect_ratio": str}
        ),
        "color": StageCapability(
            name="color", description="color", parameter_schema={"brightness": float}
        ),
    }
)


def test_empty_workflow_is_rejected() -> None:
    proposal = Proposal(summary="do nothing", workflow=[])

    result = validate_proposal(proposal, _REGISTRY)

    assert result.valid is False
    assert "workflow must not be empty" in result.errors


def test_unregistered_stage_is_rejected() -> None:
    proposal = Proposal(
        summary="...", workflow=[ProposalStage(stage="nonexistent", params={})]
    )

    result = validate_proposal(proposal, _REGISTRY)

    assert result.valid is False
    assert "unknown stage 'nonexistent'" in result.errors


def test_unknown_param_is_rejected() -> None:
    proposal = Proposal(
        summary="...",
        workflow=[ProposalStage(stage="crop", params={"not_a_real_param": "x"})],
    )

    result = validate_proposal(proposal, _REGISTRY)

    assert result.valid is False
    assert "unknown param 'not_a_real_param' for stage 'crop'" in result.errors


def test_wrong_param_type_is_rejected() -> None:
    proposal = Proposal(
        summary="...",
        workflow=[ProposalStage(stage="color", params={"brightness": "very bright"})],
    )

    result = validate_proposal(proposal, _REGISTRY)

    assert result.valid is False
    assert "param 'brightness' for stage 'color' must be float, got str" in result.errors


def test_duplicate_stage_is_rejected() -> None:
    """A deliberate validator policy, not a Setu constraint -- Setu's engine
    would mechanically tolerate a repeated stage (index-keyed stage_params,
    per-(job_id, stage) Result rows). Rejected because none of V1's five
    editing stages has a legitimate reason to run twice in one job, and a
    duplicate is more likely an LLM hallucination than intent."""
    proposal = Proposal(
        summary="...",
        workflow=[
            ProposalStage(stage="crop", params={"aspect_ratio": "9:16"}),
            ProposalStage(stage="crop", params={"aspect_ratio": "1:1"}),
        ],
    )

    result = validate_proposal(proposal, _REGISTRY)

    assert result.valid is False
    assert "duplicate stage 'crop' is not allowed" in result.errors


def test_multiple_errors_are_all_collected_not_just_the_first() -> None:
    proposal = Proposal(
        summary="...",
        workflow=[
            ProposalStage(stage="nonexistent", params={}),
            ProposalStage(stage="color", params={"brightness": "not a number"}),
        ],
    )

    result = validate_proposal(proposal, _REGISTRY)

    assert result.valid is False
    assert "unknown stage 'nonexistent'" in result.errors
    assert "param 'brightness' for stage 'color' must be float, got str" in result.errors
    assert len(result.errors) == 2


def test_valid_proposal_passes_with_no_errors() -> None:
    proposal = Proposal(
        summary="crop then brighten",
        workflow=[
            ProposalStage(stage="crop", params={"aspect_ratio": "9:16"}),
            ProposalStage(stage="color", params={"brightness": 1.1}),
        ],
    )

    result = validate_proposal(proposal, _REGISTRY)

    assert result.valid is True
    assert result.errors == []
