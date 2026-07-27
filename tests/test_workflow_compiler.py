from backend.services.proposal import Proposal, ProposalStage
from backend.services.workflow_compiler import ExecutionContext, compile_workflow


def test_compiles_to_the_exact_expected_workflow_and_payload_shape() -> None:
    proposal = Proposal(
        summary="Crop to 9:16 and brighten.",
        workflow=[
            ProposalStage(stage="crop", params={"aspect_ratio": "9:16"}),
            ProposalStage(stage="color", params={"brightness": 1.1}),
        ],
    )
    context = ExecutionContext(video_uri="local:///videos/abc.mp4")

    workflow, payload = compile_workflow(proposal, context)

    assert workflow == ["crop", "color"]
    assert payload == {
        "video_uri": "local:///videos/abc.mp4",
        "stage_params": {
            "0": {"aspect_ratio": "9:16"},
            "1": {"brightness": 1.1},
        },
    }


def test_single_stage_proposal_compiles() -> None:
    proposal = Proposal(summary="...", workflow=[ProposalStage(stage="dummy", params={})])
    context = ExecutionContext(video_uri="local:///videos/x.mp4")

    workflow, payload = compile_workflow(proposal, context)

    assert workflow == ["dummy"]
    assert payload == {"video_uri": "local:///videos/x.mp4", "stage_params": {"0": {}}}
