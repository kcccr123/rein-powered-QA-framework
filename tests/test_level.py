"""Level recipe handoffs prepare data without invoking PCG or Unity."""

from dataclasses import FrozenInstanceError
import json

import pytest

from qa_framework.models.level import LEVEL_SCHEMA_VERSION, LevelSpec


def test_unity_recipe_preserves_parameters_and_roundtrips_finite_json() -> None:
    parameters = {
        "RoomCount": 5,
        "AreaWidth": 40,
        "AreaHeight": 30,
        "MinRoomWidth": 5,
        "MaxRoomWidth": 8,
        "MinRoomHeight": 4,
        "MaxRoomHeight": 6,
        "Seed": 123,
        "FutureProducerField": {"enabled": True, "weights": [0.5, None]},
    }
    level = LevelSpec("candidate-123", 123, parameters, "game-rev/settings-rev")
    envelope = level.to_dict()

    assert envelope["schema_version"] == LEVEL_SCHEMA_VERSION == 1
    assert envelope["generation_parameters"] == parameters
    assert level.pcg_parameters() == parameters
    restored = LevelSpec.from_dict(json.loads(json.dumps(envelope, allow_nan=False)))
    assert restored == level
    assert restored.to_dict() == envelope


def test_level_parameters_are_copied_and_deeply_immutable() -> None:
    parameters = {"nested": {"rooms": [1, {"name": "original"}]}}
    level = LevelSpec("candidate", 0, parameters)
    parameters["nested"]["rooms"][1]["name"] = "changed"
    parameters["nested"]["rooms"].append(2)

    assert level.to_dict()["generation_parameters"] == {
        "nested": {"rooms": [1, {"name": "original"}]}
    }
    with pytest.raises(TypeError):
        level.generation_parameters["new"] = 1
    with pytest.raises(TypeError):
        level.generation_parameters["nested"]["rooms"][1]["name"] = "changed"
    with pytest.raises(AttributeError):
        level.generation_parameters["nested"]["rooms"].append(2)
    with pytest.raises(FrozenInstanceError):
        level.generation_seed = 1


def test_exported_parameters_and_envelopes_are_independent_copies() -> None:
    level = LevelSpec("candidate", 12, {"nested": {"items": [1]}})
    parameters = level.pcg_parameters()
    parameters["Seed"] = 13
    parameters["nested"]["items"].append(2)
    envelope = level.to_dict()
    envelope["generation_parameters"]["nested"]["items"].append(3)

    assert level.pcg_parameters() == {"nested": {"items": [1]}, "Seed": 12}


def test_empty_recipe_can_identify_an_external_level() -> None:
    level = LevelSpec("reference-only", 2**31 - 1, {})
    assert level.to_dict()["generation_parameters"] == {}
    assert level.pcg_parameters() == {"Seed": 2**31 - 1}
    assert LevelSpec.from_dict(level.to_dict()) == level


@pytest.mark.parametrize("seed", [-1, 2**31, 1.5, True, "123", None])
def test_seed_must_be_nonnegative_unity_int32(seed: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        LevelSpec("candidate", seed, {})


@pytest.mark.parametrize("seed", [13, -1, 2**31, 12.0, True, "12"])
def test_parameter_seed_must_match_typed_generation_seed(seed: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        LevelSpec("candidate", 12, {"Seed": seed})


@pytest.mark.parametrize("level_id", ["", "  ", 1, None])
def test_level_identity_is_a_nonempty_string(level_id: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        LevelSpec(level_id, 0, {})


@pytest.mark.parametrize("version", ["", " ", 1, False])
def test_generator_version_is_a_nonempty_optional_string(version: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        LevelSpec("candidate", 0, {}, version)


@pytest.mark.parametrize(
    "parameters",
    [None, [], {1: "value"}, {"nested": {1: "value"}}, {"value": object()},
     {"value": {1, 2}}, {"value": b"bytes"}, {"value": float("nan")},
     {"value": [float("inf")]}],
)
def test_recipes_reject_non_json_and_nonfinite_values(parameters: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        LevelSpec("candidate", 0, parameters)


def test_cyclic_recipes_are_rejected_and_repeated_objects_are_allowed() -> None:
    cyclic = {}
    cyclic["itself"] = cyclic
    with pytest.raises(ValueError, match="cycles"):
        LevelSpec("candidate", 0, cyclic)
    array = []
    array.append(array)
    with pytest.raises(ValueError, match="cycles"):
        LevelSpec("candidate", 0, {"array": array})

    shared = {"items": [1]}
    level = LevelSpec("candidate", 0, {"first": shared, "second": shared})
    assert level.to_dict()["generation_parameters"] == {"first": shared, "second": shared}


@pytest.mark.parametrize("version", [0, 2, 1.0, True, "1", None])
def test_handoff_rejects_unknown_or_invalid_schema_versions(version: object) -> None:
    envelope = LevelSpec("candidate", 0, {}).to_dict()
    envelope["schema_version"] = version
    with pytest.raises((TypeError, ValueError)):
        LevelSpec.from_dict(envelope)


def test_handoff_rejects_missing_required_fields_and_invalid_envelope_type() -> None:
    envelope = LevelSpec("candidate", 0, {}).to_dict()
    for field in ("schema_version", "level_id", "generation_seed", "generation_parameters"):
        malformed = dict(envelope)
        del malformed[field]
        with pytest.raises(KeyError):
            LevelSpec.from_dict(malformed)
    with pytest.raises(TypeError):
        LevelSpec.from_dict([])


def test_optional_generator_version_can_be_omitted() -> None:
    envelope = LevelSpec("candidate", 0, {}).to_dict()
    del envelope["generator_version"]
    assert LevelSpec.from_dict(envelope).generator_version is None
