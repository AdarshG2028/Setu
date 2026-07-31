"""PromptBuilder (Changelog v8) -- PlannerContext -> Prompt, extracted from
LLMPlanner so prompt engineering stays separate from the LLM call itself.
The same `build` (with an optional validation-feedback turn appended) is
reused for both the first attempt and Phase 4's one semantic retry; future
prompt types (clarification, revision, summaries) can add their own
sections here without duplicating context assembly.
"""

from dataclasses import dataclass

from backend.models import MessageRole
from backend.services.planner_context import PlannerContext


@dataclass(frozen=True)
class Prompt:
    system: str
    messages: list[dict[str, str]]


_ROLE_TO_CHAT_ROLE = {
    MessageRole.USER: "user",
    MessageRole.ASSISTANT: "assistant",
}


# Python type names mean nothing to a planner emitting JSON; these are the
# names it actually reasons about.
_JSON_TYPE_NAMES = {str: "string", int: "integer", float: "number", bool: "boolean"}

# How each stored preference reads to the planner. Phrased as a fact about
# the user rather than a column name, since that is what the model reasons
# over.
_PREFERENCE_LABELS = (
    ("preferred_platform", "publishes to"),
    ("preferred_export_format", "wants exports as"),
    ("preferred_resolution", "wants resolution"),
    ("subtitle_language", "wants subtitles in"),
)


def _video_facts(video: object) -> str:
    """What analysis measured about this video, appended to its line.

    Rendered inline rather than as a separate block so a handle and its
    facts can never drift apart in a multi-video project. Duration is
    given in seconds because every time-based capability (trim) takes
    seconds, so the planner needs no conversion to use it.
    """
    facts = []
    duration = getattr(video, "duration_seconds", None)
    if duration:
        facts.append(f"{duration:g}s long")
    if resolution := getattr(video, "resolution", None):
        orientation = getattr(video, "orientation", None)
        facts.append(f"{resolution}{f' {orientation}' if orientation else ''}")
    return f" ({', '.join(facts)})" if facts else ""


def _render_preferences(preferences: object | None) -> list[str]:
    """Standing preferences, as lines the planner can actually read.

    This used to interpolate the ORM object directly, which rendered as
    `<UserPreference object at 0x...>` -- the model was being handed a
    Python repr and, unsurprisingly, ignored it. The bug survived because
    nothing wrote preferences until Phase 6, so every prior run had none
    and the branch never produced anything meaningful.

    Only fields that are actually set are rendered: a line saying a
    preference is unset is noise that invites the model to treat absence
    as a decision.
    """
    if preferences is None:
        return []

    facts = [
        f"- {label}: {value}"
        for field, label in _PREFERENCE_LABELS
        if (value := getattr(preferences, field, None))
    ]
    if getattr(preferences, "captions_enabled", False):
        facts.append("- always wants captions burned in")

    if not facts:
        return []
    return [
        "What this user has told you before, in earlier sessions. Apply these "
        "unless the current request says otherwise, and do not ask about them "
        "again:",
        *facts,
    ]


class PromptBuilder:
    def build(
        self, context: PlannerContext, *, validation_feedback: list[str] | None = None
    ) -> Prompt:
        system = self._build_system(context)
        messages = [
            {"role": _ROLE_TO_CHAT_ROLE[m.role], "content": m.content}
            for m in context.conversation_history
        ]
        if validation_feedback:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Your previous proposal was invalid for these reasons: "
                        + "; ".join(validation_feedback)
                        + ". Please produce a corrected proposal, or ask a clarifying "
                        "question if you're missing information needed to fix it."
                    ),
                }
            )
        return Prompt(system=system, messages=messages)

    def _build_system(self, context: PlannerContext) -> str:
        lines = [
            "You are a video editing assistant. You never edit video yourself -- "
            "you either ask a clarifying question or propose a workflow of stages "
            "for a deterministic execution engine to run.",
            "",
            "Respond with `{\"type\": \"message\", ...}` if you need more information, "
            "or `{\"type\": \"proposal\", ...}` once you have enough to propose a "
            "concrete edit.",
            "",
            "Available videos in this project (reference by handle in `video_ids`, "
            "never by any other identifier):",
        ]
        for video in context.videos:
            lines.append(f"- {video.handle}: {video.display_name}{_video_facts(video)}")

        lines.append("")
        lines.append("Available stages (only use these in `workflow`):")
        for capability in context.capability_registry.list():
            lines.append(f"- {capability.name}: {capability.description}")
            # The parameter names must come from the schema, not from the
            # prose above. Before Phase 5 the only capability was `dummy`,
            # which takes none, so this was invisible; once capabilities
            # had real params the planner was left inferring key names from
            # the description -- which worked for `aspect_ratio` and
            # `brightness` by luck of phrasing, and failed for `rotate`,
            # where it asked the *user* what the parameter was called.
            if capability.parameter_schema:
                rendered = ", ".join(
                    f"{name} ({_JSON_TYPE_NAMES.get(type_, type_.__name__)})"
                    for name, type_ in sorted(capability.parameter_schema.items())
                )
                lines.append(f"  parameters: {rendered}")

        rendered_preferences = _render_preferences(context.preferences)
        if rendered_preferences:
            lines.append("")
            lines.extend(rendered_preferences)

        if context.approval_policy is not None:
            lines.append("")
            lines.append(f"Approval policy for this room: {context.approval_policy}")

        return "\n".join(lines)
