"""get_default_planner (Changelog v8) -- the Planner the running API
actually uses per request.

LLMPlanner (backed by GroqClient) when GROQ_API_KEY is configured;
StaticPlanner otherwise, so a checkout with no key still boots and the
Phase 2 conversation loop still works (also what every existing test
exercises, since no test environment sets GROQ_API_KEY). Cached like
get_settings() so GroqClient's underlying SDK client isn't rebuilt per
request.
"""

from functools import lru_cache

from backend.core.config import get_settings
from backend.services.llm_client import GroqClient
from backend.services.llm_planner import LLMPlanner
from backend.services.planner import Planner, StaticPlanner


@lru_cache
def get_default_planner() -> Planner:
    settings = get_settings()
    if not settings.groq_api_key:
        return StaticPlanner()
    client = GroqClient(api_key=settings.groq_api_key, model=settings.groq_model)
    return LLMPlanner(client)
