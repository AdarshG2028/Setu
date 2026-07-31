"""Capability registry (§6): the one source of truth for which stages a
proposal may reference and what parameters each accepts. Doubles as the
future LLM prompt's contract ("here's what you're allowed to choose from")
and ProposalValidator's runtime contract ("here's what's actually
allowed").

A class, not a bare module-level dict, so ProposalValidator depends on this
interface rather than a global constant -- Phase 5 registers each real
editing capability here, one at a time, without touching validator logic.

Capabilities, not workers, is the seam the planner sees: it only ever
reads names, descriptions, parameter schemas and asset kinds from here,
and never learns that a Worker subclass or a Kafka topic exists. That is
what lets an implementation change (splitting a worker, swapping ffmpeg
for something else) happen without the planner noticing.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StageCapability:
    name: str
    description: str
    # Known param keys -> expected Python type. Known-keys-plus-basic-type-
    # check is enough for V1 (§6); not a full JSON-Schema validator.
    parameter_schema: dict[str, type] = field(default_factory=dict)

    # What this stage needs to already exist, and what it leaves behind
    # (Phase 5's asset model -- see backend/workers/media.py). Kinds are
    # plain strings rather than an import of AssetKind: this registry is a
    # planner-facing declaration of what may be proposed, and pointing it
    # at a worker implementation module would invert that layering.
    # AssetKind lists the canonical spellings.
    #
    # The defaults describe the overwhelmingly common case -- takes a
    # video, returns a video -- so crop/color/audio/trim/merge/render all
    # need no extra declaration. Only the transcript pair diverges:
    # transcribe additionally produces "transcript"/"srt", and
    # burn_subtitles additionally requires "srt".
    #
    # validate_proposal walks these in order to reject a proposal that
    # consumes an asset nothing upstream produces, so the planner's
    # retry loop can fix it rather than the job dying at execution time.
    requires_asset_kinds: tuple[str, ...] = ("video",)
    produces_asset_kinds: tuple[str, ...] = ("video",)


class CapabilityRegistry:
    def __init__(self, capabilities: dict[str, StageCapability] | None = None) -> None:
        self._capabilities = dict(capabilities or {})

    def register(self, capability: StageCapability) -> None:
        self._capabilities[capability.name] = capability

    def exists(self, stage_name: str) -> bool:
        return stage_name in self._capabilities

    def get(self, stage_name: str) -> StageCapability | None:
        return self._capabilities.get(stage_name)

    def list(self) -> list[StageCapability]:
        return list(self._capabilities.values())


# `dummy` (backend/workers/dummy_worker.py) is a stand-in stage proving the
# validate -> compile -> submit -> execute wiring end to end, not a real
# editing operation. It stays registered after Phase 5 lands real
# capabilities: the Setu-core infrastructure tests (retry/DLQ, crash
# recovery) deliberately drive a worker that needs no ffmpeg.
#
# Real capabilities are registered here one sub-phase at a time (§19 Phase
# 5): crop/resize/rotate/flip/pad (5B), color (5C), audio (5D), trim/merge
# (5E), transcribe/burn_subtitles (5F), render (5G).
DEFAULT_CAPABILITY_REGISTRY = CapabilityRegistry(
    {
        "dummy": StageCapability(
            name="dummy",
            description=(
                "No-op stand-in stage used to prove the proposal pipeline "
                "end to end. Not a real editing operation."
            ),
            parameter_schema={},
        ),
        # Descriptions here are the planner's entire understanding of a
        # capability -- it never sees the worker. So they say what the stage
        # is *for*, in the words a user would use ("vertical", "Reels"),
        # not how it is implemented.
        "crop": StageCapability(
            name="crop",
            description=(
                "Reframe the video to a target aspect ratio by cropping to the "
                "centre of the frame, filling it edge to edge with no black bars. "
                "Use for converting landscape footage to vertical for Reels, "
                "Shorts or TikTok ('9:16'), to square for feed posts ('1:1'), or "
                "back to widescreen ('16:9'). Content near the edges is cut off; "
                "use 'pad' instead when nothing may be lost."
            ),
            parameter_schema={"aspect_ratio": str},
        ),
        "color": StageCapability(
            name="color",
            description=(
                "Adjust the look of the picture: brightness, contrast, saturation, "
                "gamma and sharpening, in any combination. Use for footage that is "
                "too dark or flat, or to make it more vivid and punchy. Values are "
                "multipliers around a neutral point -- brightness 0 and contrast, "
                "saturation and gamma 1 mean 'unchanged', so pass only what should "
                "change. Typical edits are small: brightness 0.1, contrast 1.2, "
                "saturation 1.3. This does not move, resize or reframe the picture."
            ),
            parameter_schema={
                "brightness": float,
                "contrast": float,
                "saturation": float,
                "gamma": float,
                "sharpen": float,
            },
        ),
        "resize": StageCapability(
            name="resize",
            description=(
                "Scale the video to explicit pixel dimensions, e.g. 1920x1080 or "
                "720p. Give only width or only height to scale proportionally; "
                "give both to force an exact size, which may stretch the picture. "
                "Use this for a size requirement ('make it 1080p'); use 'crop' or "
                "'pad' instead for a shape requirement ('make it vertical')."
            ),
            parameter_schema={"width": int, "height": int},
        ),
        "rotate": StageCapability(
            name="rotate",
            description=(
                "Rotate the picture by a quarter turn: 90, 180 or 270 degrees "
                "clockwise. Use for footage filmed with the camera held the wrong "
                "way up. Only these three angles are supported."
            ),
            parameter_schema={"degrees": int},
        ),
        "flip": StageCapability(
            name="flip",
            description=(
                "Mirror the picture, either 'horizontal' (left-right) or "
                "'vertical' (upside down). Horizontal is the common one: it "
                "un-mirrors selfie-camera footage so text reads the right way."
            ),
            parameter_schema={"direction": str},
        ),
        "pad": StageCapability(
            name="pad",
            description=(
                "Fit the video to a target aspect ratio by adding bars around it, "
                "keeping the entire picture visible. The counterpart to 'crop': "
                "use pad when nothing may be cut off (a screen recording, a chart, "
                "anything with text at the edges), and crop when the frame should "
                "be filled edge to edge. pad_color defaults to black."
            ),
            parameter_schema={"aspect_ratio": str, "pad_color": str},
        ),
    }
)
