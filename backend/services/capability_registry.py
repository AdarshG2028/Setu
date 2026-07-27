"""Capability registry (§6): the one source of truth for which stages a
proposal may reference and what parameters each accepts. Doubles as the
future LLM prompt's contract ("here's what you're allowed to choose from")
and ProposalValidator's runtime contract ("here's what's actually
allowed").

A class, not a bare module-level dict, so ProposalValidator depends on this
interface rather than a global constant -- Phase 5 registers each real
editing worker here, one at a time, without touching validator logic.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StageCapability:
    name: str
    description: str
    # Known param keys -> expected Python type. Known-keys-plus-basic-type-
    # check is enough for V1 (§6); not a full JSON-Schema validator.
    parameter_schema: dict[str, type] = field(default_factory=dict)


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


# V1: only the existing `dummy` worker (backend/workers/dummy_worker.py),
# registered as a stand-in stage to prove the validate -> compile -> submit
# -> execute wiring end to end -- not a real editing operation. Real
# editing stages (crop/color/audio/subtitle/export) are registered here one
# at a time starting Phase 5 (§19).
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
    }
)
