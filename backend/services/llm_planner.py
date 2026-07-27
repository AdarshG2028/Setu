"""LLMPlanner (Changelog v8) -- Phase 4's real planner, replacing
StaticPlanner behind the same Planner interface. Stateless: every call gets
a fresh PlannerContext and returns a PlannerResponse, nothing more -- no DB
access, no job submission, no memory writes, no orchestration, no hidden
state between calls.

Two independent retry layers:
  - Infrastructure retry (in `_complete_with_retry`): network failure,
    provider timeout, or non-parseable structured output -- LLMClientError,
    raised by the LLMClient layer. Retried up to `infra_retry_attempts`
    times with nothing else changing about the request.
  - Semantic retry (in `respond`): validate_proposal (Phase 3, unmodified)
    rejects the proposal -> regenerate exactly once with the validation
    errors fed back into the prompt -> if that still fails, return a
    friendly clarification message instead of ever reaching a job. No
    recursive retries, no reflection/self-critique loop, no unbounded
    regeneration -- the literal gate the v6 AI-architecture principles call
    for at this phase.
"""

import logging

from backend.services.llm_client import LLMClient, LLMClientError
from backend.services.planner import Planner
from backend.services.planner_context import PlannerContext
from backend.services.planner_response import PLANNER_RESPONSE_SCHEMA, PlannerResponse
from backend.services.prompt_builder import Prompt, PromptBuilder
from backend.services.proposal_validator import validate_proposal

logger = logging.getLogger(__name__)

DEFAULT_INFRA_RETRY_ATTEMPTS = 3

_COULD_NOT_PLAN_MESSAGE = (
    "I wasn't able to put together a valid plan for that -- could you "
    "clarify what you'd like done?"
)


class LLMPlanner(Planner):
    def __init__(
        self,
        client: LLMClient,
        *,
        prompt_builder: PromptBuilder | None = None,
        infra_retry_attempts: int = DEFAULT_INFRA_RETRY_ATTEMPTS,
    ) -> None:
        self._client = client
        self._prompt_builder = prompt_builder or PromptBuilder()
        self._infra_retry_attempts = infra_retry_attempts

    async def respond(self, context: PlannerContext) -> PlannerResponse:
        prompt = self._prompt_builder.build(context)
        response = await self._complete(prompt)

        if response.type != "proposal":
            self._log(regenerated=False, validation_success=True)
            return response

        validation = validate_proposal(response.proposal, context.capability_registry)
        if validation.valid:
            self._log(regenerated=False, validation_success=True)
            return response

        retry_prompt = self._prompt_builder.build(context, validation_feedback=validation.errors)
        retry_response = await self._complete(retry_prompt)

        if retry_response.type != "proposal":
            # The regenerated turn asked a clarifying question instead of
            # proposing again -- a legitimate response on its own.
            self._log(regenerated=True, validation_success=True)
            return retry_response

        retry_validation = validate_proposal(retry_response.proposal, context.capability_registry)
        if retry_validation.valid:
            self._log(regenerated=True, validation_success=True)
            return retry_response

        self._log(regenerated=True, validation_success=False)
        return PlannerResponse(type="message", message=_COULD_NOT_PLAN_MESSAGE)

    async def _complete(self, prompt: Prompt) -> PlannerResponse:
        raw = await self._complete_with_retry(prompt)
        return PlannerResponse.from_dict(raw)

    async def _complete_with_retry(self, prompt: Prompt) -> dict:
        last_error: LLMClientError | None = None
        for attempt in range(1, self._infra_retry_attempts + 1):
            try:
                return await self._client.complete(
                    prompt, response_schema=PLANNER_RESPONSE_SCHEMA
                )
            except LLMClientError as exc:
                last_error = exc
                logger.warning(
                    "llm_client call failed, retrying",
                    extra={"attempt": attempt, "error": str(exc)},
                )
        assert last_error is not None
        raise last_error

    def _log(self, *, regenerated: bool, validation_success: bool) -> None:
        logger.info(
            "llm_planner turn completed",
            extra={"regeneration_used": regenerated, "validation_success": validation_success},
        )
