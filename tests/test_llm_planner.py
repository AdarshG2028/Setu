"""LLMPlanner's two retry layers, exercised against a FakeLLMClient so no
real Groq call is needed -- infra retry (network/timeout/malformed output)
and the exactly-one semantic retry (validate_proposal rejection ->
regenerate once with feedback -> friendly message if still invalid).
"""

import uuid

import pytest

from backend.services.capability_registry import DEFAULT_CAPABILITY_REGISTRY
from backend.services.llm_client import LLMClient, LLMClientError
from backend.services.llm_planner import LLMPlanner
from backend.services.planner_context import PlannerContext
from backend.services.prompt_builder import Prompt


class FakeLLMClient(LLMClient):
    """Returns/raises each entry in `results` in sequence, one per call."""

    def __init__(self, results: list) -> None:
        self._results = list(results)
        self.calls = 0

    async def complete(self, prompt: Prompt, *, response_schema: dict) -> dict:
        self.calls += 1
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _context() -> PlannerContext:
    return PlannerContext(
        project=None,
        conversation_history=[],
        videos=[],
        preferences=None,
        capability_registry=DEFAULT_CAPABILITY_REGISTRY,
    )


VALID_MESSAGE = {"type": "message", "text": "who is this for?", "summary": None, "workflow": None}
VALID_PROPOSAL = {
    "type": "proposal",
    "text": None,
    "summary": "Run the dummy stage.",
    "workflow": [{"stage": "dummy", "video_ids": [], "params": {}}],
}
INVALID_PROPOSAL = {
    "type": "proposal",
    "text": None,
    "summary": "Run an unregistered stage.",
    "workflow": [{"stage": "not-a-real-stage", "video_ids": [], "params": {}}],
}


@pytest.mark.asyncio
async def test_message_response_passes_through_with_no_retry() -> None:
    client = FakeLLMClient([VALID_MESSAGE])
    planner = LLMPlanner(client)

    response = await planner.respond(_context())

    assert response.type == "message"
    assert response.message == "who is this for?"
    assert client.calls == 1


@pytest.mark.asyncio
async def test_valid_proposal_passes_through_with_no_retry() -> None:
    client = FakeLLMClient([VALID_PROPOSAL])
    planner = LLMPlanner(client)

    response = await planner.respond(_context())

    assert response.type == "proposal"
    assert response.proposal.workflow[0].stage == "dummy"
    assert client.calls == 1


@pytest.mark.asyncio
async def test_invalid_proposal_regenerates_once_and_succeeds() -> None:
    client = FakeLLMClient([INVALID_PROPOSAL, VALID_PROPOSAL])
    planner = LLMPlanner(client)

    response = await planner.respond(_context())

    assert response.type == "proposal"
    assert response.proposal.workflow[0].stage == "dummy"
    assert client.calls == 2


@pytest.mark.asyncio
async def test_invalid_proposal_twice_returns_friendly_clarification_not_a_third_attempt() -> None:
    client = FakeLLMClient([INVALID_PROPOSAL, INVALID_PROPOSAL])
    planner = LLMPlanner(client)

    response = await planner.respond(_context())

    assert response.type == "message"
    assert client.calls == 2  # exactly one regeneration, never a third attempt


@pytest.mark.asyncio
async def test_infra_failure_retries_then_succeeds() -> None:
    client = FakeLLMClient([LLMClientError("timeout"), LLMClientError("timeout"), VALID_MESSAGE])
    planner = LLMPlanner(client, infra_retry_attempts=3)

    response = await planner.respond(_context())

    assert response.type == "message"
    assert client.calls == 3


@pytest.mark.asyncio
async def test_infra_failure_exhausts_retries_and_raises() -> None:
    client = FakeLLMClient(
        [LLMClientError("timeout"), LLMClientError("timeout"), LLMClientError("timeout")]
    )
    planner = LLMPlanner(client, infra_retry_attempts=3)

    with pytest.raises(LLMClientError):
        await planner.respond(_context())

    assert client.calls == 3
