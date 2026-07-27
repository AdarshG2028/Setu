import uuid

from backend.models import Message, MessageRole, Project
from backend.services.capability_registry import DEFAULT_CAPABILITY_REGISTRY
from backend.services.planner_context import PlannerContext, VideoContext
from backend.services.prompt_builder import PromptBuilder


def _context(*, history=None, videos=None, validation_feedback=None) -> PlannerContext:
    return PlannerContext(
        project=Project(id=uuid.uuid4(), owner_id=uuid.uuid4()),
        conversation_history=history or [],
        videos=videos or [],
        preferences=None,
        capability_registry=DEFAULT_CAPABILITY_REGISTRY,
    )


def test_system_prompt_lists_video_handles_and_capabilities() -> None:
    context = _context(
        videos=[VideoContext(handle="video_1", video_id=str(uuid.uuid4()), display_name="Intro")]
    )

    prompt = PromptBuilder().build(context)

    assert "video_1: Intro" in prompt.system
    assert "dummy" in prompt.system


def test_conversation_history_maps_to_chat_roles() -> None:
    history = [
        Message(conversation_id=uuid.uuid4(), role=MessageRole.USER, content="hi"),
        Message(conversation_id=uuid.uuid4(), role=MessageRole.ASSISTANT, content='{"type": "message", "text": "ok"}'),
    ]

    prompt = PromptBuilder().build(_context(history=history))

    assert prompt.messages == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": '{"type": "message", "text": "ok"}'},
    ]


def test_validation_feedback_appends_one_extra_user_turn() -> None:
    prompt = PromptBuilder().build(_context(), validation_feedback=["unknown stage 'crp'"])

    assert len(prompt.messages) == 1
    assert prompt.messages[0]["role"] == "user"
    assert "unknown stage 'crp'" in prompt.messages[0]["content"]


def test_no_validation_feedback_appends_nothing() -> None:
    prompt = PromptBuilder().build(_context())

    assert prompt.messages == []
