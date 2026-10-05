"""Validation for generic episode limits, metrics, and outcomes."""

from dataclasses import FrozenInstanceError

import pytest

from qa_framework.evaluation.result import EpisodeResult, TerminalReason
from qa_framework.models.episode import EpisodeConfig
from qa_framework.models.metrics import EpisodeMetrics


def test_metadata_is_copied_and_top_level_read_only() -> None:
    metadata = {"level_id": "external-level"}
    config = EpisodeConfig(metadata=metadata)
    metadata["level_id"] = "changed"
    assert config.metadata["level_id"] == "external-level"
    with pytest.raises(TypeError):
        config.metadata["level_id"] = "another"
    with pytest.raises(FrozenInstanceError):
        config.max_steps = 10
    assert EpisodeConfig().metadata is not EpisodeConfig().metadata


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "10"])
def test_max_steps_must_be_positive_integer(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        EpisodeConfig(max_steps=value)


@pytest.mark.parametrize("value", [0, -0.1, float("nan"), float("inf"), True, "2"])
def test_max_duration_must_be_positive_finite_number(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        EpisodeConfig(max_duration_seconds=value)


@pytest.mark.parametrize("metadata", [None, [], {1: "value"}])
def test_metadata_must_be_mapping_with_string_keys(metadata: object) -> None:
    with pytest.raises(TypeError):
        EpisodeConfig(metadata=metadata)


@pytest.mark.parametrize("field", ["step_count", "enemies_killed", "powerups_used", "stuck_events"])
@pytest.mark.parametrize("value", [-1, True, 0.5])
def test_metric_counts_are_nonnegative_integers(field: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        EpisodeMetrics(**{field: value})


@pytest.mark.parametrize("field", ["elapsed_seconds", "damage_taken"])
@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True])
def test_metric_numbers_are_nonnegative_and_finite(field: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        EpisodeMetrics(**{field: value})


@pytest.mark.parametrize("field", ["completed", "player_died"])
def test_metric_flags_require_booleans(field: str) -> None:
    with pytest.raises(TypeError):
        EpisodeMetrics(**{field: 1})


@pytest.mark.parametrize("reason", list(TerminalReason))
def test_result_has_consistent_termination_flags(reason: TerminalReason) -> None:
    is_interruption = reason in (
        TerminalReason.MAX_STEPS,
        TerminalReason.MAX_DURATION,
        TerminalReason.ENVIRONMENT_FAILURE,
    )
    result = EpisodeResult(
        terminated=not is_interruption,
        truncated=is_interruption,
        terminal_reason=reason,
        metrics=EpisodeMetrics(),
    )
    assert result.terminal_reason is reason


@pytest.mark.parametrize("flags", [(False, False), (True, True)])
def test_result_represents_one_end_condition(flags: tuple[bool, bool]) -> None:
    with pytest.raises(ValueError):
        EpisodeResult(*flags, TerminalReason.COMPLETED, EpisodeMetrics())


@pytest.mark.parametrize(
    "reason,terminated,truncated",
    [
        (TerminalReason.MAX_STEPS, True, False),
        (TerminalReason.COMPLETED, False, True),
        (TerminalReason.ENVIRONMENT_FAILURE, True, False),
    ],
)
def test_result_rejects_reason_flag_mismatch(
    reason: TerminalReason, terminated: bool, truncated: bool
) -> None:
    with pytest.raises(ValueError):
        EpisodeResult(terminated, truncated, reason, EpisodeMetrics())


def test_result_requires_typed_reason_and_metrics() -> None:
    with pytest.raises(TypeError):
        EpisodeResult(True, False, "completed", EpisodeMetrics())
    with pytest.raises(TypeError):
        EpisodeResult(True, False, TerminalReason.COMPLETED, {})
