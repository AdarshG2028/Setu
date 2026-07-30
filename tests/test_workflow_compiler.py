from backend.services.proposal import Proposal, ProposalStage
from backend.services.workflow_compiler import ExecutionContext, compile_workflow


def test_compiles_to_the_exact_expected_workflow_and_payload_shape() -> None:
    proposal = Proposal(
        summary="Crop to 9:16 and brighten.",
        workflow=[
            ProposalStage(stage="crop", video_ids=["video_1"], params={"aspect_ratio": "9:16"}),
            ProposalStage(stage="color", video_ids=["video_1"], params={"brightness": 1.1}),
        ],
    )
    context = ExecutionContext(video_uris={"video_1": "local:///videos/abc.mp4"})

    workflow, payload = compile_workflow(proposal, context)

    assert workflow == ["crop", "color"]
    assert payload == {
        "stage_params": {
            "0": {"params": {"aspect_ratio": "9:16"}, "video_uris": ["local:///videos/abc.mp4"]},
            "1": {"params": {"brightness": 1.1}, "video_uris": ["local:///videos/abc.mp4"]},
        },
    }


def test_single_stage_proposal_compiles() -> None:
    proposal = Proposal(summary="...", workflow=[ProposalStage(stage="dummy", params={})])
    context = ExecutionContext(video_uris={})

    workflow, payload = compile_workflow(proposal, context)

    assert workflow == ["dummy"]
    assert payload == {"stage_params": {"0": {"params": {}, "video_uris": []}}}


def test_stage_referencing_multiple_videos_resolves_all_of_them() -> None:
    proposal = Proposal(
        summary="Combine two clips.",
        workflow=[ProposalStage(stage="combine", video_ids=["video_1", "video_2"], params={})],
    )
    context = ExecutionContext(
        video_uris={
            "video_1": "local:///videos/a.mp4",
            "video_2": "local:///videos/b.mp4",
        }
    )

    workflow, payload = compile_workflow(proposal, context)

    assert workflow == ["combine"]
    assert payload["stage_params"]["0"]["video_uris"] == [
        "local:///videos/a.mp4",
        "local:///videos/b.mp4",
    ]
