from backend.services.capability_registry import (
    DEFAULT_CAPABILITY_REGISTRY,
    CapabilityRegistry,
    StageCapability,
)


def test_register_then_get_returns_it() -> None:
    registry = CapabilityRegistry()
    capability = StageCapability(name="crop", description="crop a video")

    registry.register(capability)

    assert registry.get("crop") is capability


def test_get_returns_none_for_unregistered_stage() -> None:
    registry = CapabilityRegistry()

    assert registry.get("nonexistent") is None


def test_exists_reflects_registration_state() -> None:
    registry = CapabilityRegistry()
    assert registry.exists("crop") is False

    registry.register(StageCapability(name="crop", description="crop a video"))

    assert registry.exists("crop") is True


def test_list_returns_every_registered_capability() -> None:
    registry = CapabilityRegistry()
    registry.register(StageCapability(name="crop", description="crop"))
    registry.register(StageCapability(name="color", description="color"))

    names = {capability.name for capability in registry.list()}

    assert names == {"crop", "color"}


def test_constructor_accepts_a_seed_dict() -> None:
    seed = {"dummy": StageCapability(name="dummy", description="no-op")}

    registry = CapabilityRegistry(seed)

    assert registry.exists("dummy") is True


def test_default_registry_has_the_dummy_stand_in_stage() -> None:
    assert DEFAULT_CAPABILITY_REGISTRY.exists("dummy") is True
    assert DEFAULT_CAPABILITY_REGISTRY.get("dummy").parameter_schema == {}
