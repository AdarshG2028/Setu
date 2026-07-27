"""Planner — decides what to say back in a conversation.

The interface the rest of the app depends on. `StaticPlanner` is Phase 2's
deterministic placeholder; Phase 4 adds `LLMPlanner` behind the same
interface, and no caller (ConversationService, the routes) changes.

Response shape is the long-term one, not a temporary Phase-2 format —
`{"type": "message", ...}` or `{"type": "proposal", ...}` (see the
architecture doc's planner output schema). "proposal", not "plan": later
phases add proposal approval, and this way that never means renaming an
API that already shipped.
"""

from abc import ABC, abstractmethod
from typing import Any

from backend.models import Message, MessageRole, UserPreference


class Planner(ABC):
    @abstractmethod
    async def respond(
        self, messages: list[Message], preferences: UserPreference | None
    ) -> dict[str, Any]:
        """`messages` is the conversation so far, oldest first, including the
        turn just posted. Returns a `{"type": "message"}` or
        `{"type": "proposal"}` payload -- never executes anything itself."""
        raise NotImplementedError


class StaticPlanner(Planner):
    """Deterministic stand-in: no LLM, same shape a real planner will return.

    First user turn in the conversation gets a clarifying question; every
    turn after that gets the same fixed proposal. The content is a
    placeholder -- only the shape is load-bearing for Phase 2.
    """

    async def respond(
        self, messages: list[Message], preferences: UserPreference | None
    ) -> dict[str, Any]:
        user_turns = sum(1 for m in messages if m.role == MessageRole.USER)
        if user_turns <= 1:
            return {
                "type": "message",
                "text": "What would you like to do with this video?",
            }
        return {
            "type": "proposal",
            "summary": "Crop to 9:16 and normalize audio.",
            "workflow": [
                {"stage": "crop", "params": {"aspect_ratio": "9:16"}},
                {"stage": "audio", "params": {"normalize": True}},
            ],
        }
