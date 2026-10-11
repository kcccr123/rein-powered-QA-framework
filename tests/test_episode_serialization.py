"""Finite JSON episode handoffs retain the local model validation rules."""

import json

import pytest

from qa_framework.evaluation.result import EpisodeResult, TerminalReason
from qa_framework.models.episode import EpisodeConfig
from qa_framework.models.level import LevelSpec
from qa_framework.models.metrics import EpisodeMetrics


def _json_roundtrip(data: dict[str, object]) -> dict[str, object]:
    return json.loads(json.dumps(data, allow_nan=False))


def test_episode_config_roundtrips_limits_metadata_and_level() -> None:
    config = EpisodeConfig(
        40,
        12.5,
        {"run": "qa-run", "training": {"weights": [1, 0.5]}, "optional": None},
        LevelSpec("candidate", 123, {"RoomCount": 5}),
    )
    assert EpisodeConfig.from_dict(_json_roundtrip(config.to_dict())) == config
    assert config.to_dict()["level"] == config.level.to_dict()


def test_config_keeps_existing_positional_arguments_and_optional_level() -> None:
    config = EpisodeConfig(10, None, {"context": "local"})
    assert config.level is None
    assert EpisodeConfig.from_dict(_json_roundtrip(config.to_dict())) == config
    legacy_data = config.to_dict()
    del legacy_data["level"]
    assert EpisodeConfig.from_dict(legacy_data) == config


def test_config_exports_deep_copies_of_json_metadata() -> None:
    config = EpisodeConfig(metadata={"nested": {"items": [1]}})
    serialized = config.to_dict()
    restored = EpisodeConfig.from_dict(serialized)
    serialized["metadata"]["nested"]["items"].append(2)
    assert config.metadata["nested"]["items"] == [1]
    assert restored.metadata["nested"]["items"] == [1]


def test_local_metadata_can_remain_arbitrary_but_cannot_be_serialized() -> None:
    context = object()
    config = EpisodeConfig(metadata={"local_context": context})
    assert config.metadata["local_context"] is context
    with pytest.raises(TypeError, match="JSON"):
        config.to_dict()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_serialization_rejects_nonfinite_metadata(value: float) -> None:
    config = EpisodeConfig(metadata={"nested": [value]})
    with pytest.raises(ValueError, match="finite"):
        config.to_dict()


def test_config_requires_typed_level_context() -> None:
    with pytest.raises(TypeError, match="LevelSpec"):
        EpisodeConfig(level={"level_id": "candidate"})
    serialized = EpisodeConfig().to_dict()
    serialized["level"] = "candidate"
    with pytest.raises(TypeError):
        EpisodeConfig.from_dict(serialized)


@pytest.mark.parametrize(
    "field,value",
    [("max_steps", True), ("max_duration_seconds", "1"), ("metadata", [])],
)
def test_config_deserialization_does_not_coerce_invalid_types(
    field: str, value: object
) -> None:
    serialized = EpisodeConfig().to_dict()
    serialized[field] = value
    with pytest.raises((TypeError, ValueError)):
        EpisodeConfig.from_dict(serialized)


def test_metrics_roundtrip_all_descriptive_fields() -> None:
    metrics = EpisodeMetrics(
        step_count=40,
        elapsed_seconds=12.5,
        completed=True,
        player_died=False,
        enemies_killed=7,
        damage_taken=3.25,
        powerups_used=2,
        stuck_events=1,
    )
    assert EpisodeMetrics.from_dict(_json_roundtrip(metrics.to_dict())) == metrics


@pytest.mark.parametrize(
    "field,value",
    [("step_count", True), ("elapsed_seconds", "1"), ("completed", 1),
     ("player_died", "false"), ("damage_taken", float("nan")),
     ("enemies_killed", -1), ("powerups_used", 0.5), ("stuck_events", -1)],
)
def test_metrics_deserialization_does_not_coerce_invalid_values(
    field: str, value: object
) -> None:
    serialized = EpisodeMetrics().to_dict()
    serialized[field] = value
    with pytest.raises((TypeError, ValueError)):
        EpisodeMetrics.from_dict(serialized)


@pytest.mark.parametrize("reason", list(TerminalReason))
def test_result_roundtrips_termination_reason_and_metrics(reason: TerminalReason) -> None:
    terminated = reason in (TerminalReason.COMPLETED, TerminalReason.PLAYER_DIED)
    metrics = EpisodeMetrics(
        step_count=3,
        elapsed_seconds=0.5,
        completed=reason is TerminalReason.COMPLETED,
        player_died=reason is TerminalReason.PLAYER_DIED,
    )
    result = EpisodeResult(terminated, not terminated, reason, metrics)
    serialized = _json_roundtrip(result.to_dict())
    assert serialized["terminal_reason"] == reason.value
    assert EpisodeResult.from_dict(serialized) == result


@pytest.mark.parametrize(
    "field,value",
    [("terminated", 1), ("truncated", True), ("terminal_reason", "unknown"),
     ("terminal_reason", 1), ("metrics", [])],
)
def test_result_deserialization_retains_lifecycle_validation(
    field: str, value: object
) -> None:
    serialized = EpisodeResult(True, False, TerminalReason.COMPLETED, EpisodeMetrics()).to_dict()
    serialized[field] = value
    with pytest.raises((TypeError, ValueError)):
        EpisodeResult.from_dict(serialized)
