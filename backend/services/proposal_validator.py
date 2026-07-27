"""Validates a Proposal against a CapabilityRegistry (§6), before it's ever
compiled into a Setu job.

Collects every error found rather than stopping at the first: Phase 4's
regenerate-and-retry loop feeds the full ValidationResult back into the
planner's prompt, and "unknown stage 'crp', unknown param 'britness' on
'color'" is a better retry signal than one error at a time.
"""

from dataclasses import dataclass, field

from backend.services.capability_registry import (
    DEFAULT_CAPABILITY_REGISTRY,
    CapabilityRegistry,
)
from backend.services.proposal import Proposal


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


def validate_proposal(
    proposal: Proposal, registry: CapabilityRegistry = DEFAULT_CAPABILITY_REGISTRY
) -> ValidationResult:
    if not proposal.workflow:
        return ValidationResult(valid=False, errors=["workflow must not be empty"])

    errors: list[str] = []
    seen_stages: set[str] = set()

    for item in proposal.workflow:
        if item.stage in seen_stages:
            errors.append(f"duplicate stage '{item.stage}' is not allowed")
        seen_stages.add(item.stage)

        capability = registry.get(item.stage)
        if capability is None:
            errors.append(f"unknown stage '{item.stage}'")
            continue

        for key, value in item.params.items():
            if key not in capability.parameter_schema:
                errors.append(f"unknown param '{key}' for stage '{item.stage}'")
                continue
            expected_type = capability.parameter_schema[key]
            if not isinstance(value, expected_type):
                errors.append(
                    f"param '{key}' for stage '{item.stage}' must be "
                    f"{expected_type.__name__}, got {type(value).__name__}"
                )

    return ValidationResult(valid=not errors, errors=errors)
