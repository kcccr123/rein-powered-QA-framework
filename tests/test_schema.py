"""Stable flat observation layout, independent of any ML backend."""

import pytest

from qa_framework import (
    DEFAULT_RAY_COUNT,
    OBSERVATION_SCHEMA_VERSION,
    QAAction,
    QAObjectType,
    QAObservation,
    RayObservation,
    flatten_observation,
    get_flat_observation_size,
)


def make_observation(ray_count: int, action: QAAction | None = None) -> QAObservation:
    return QAObservation(
        health_01=1.0,
        powerup_count=0,
        rays=tuple(RayObservation(False, 1.0, QAObjectType.NONE) for _ in range(ray_count)),
        previous_action=action if action is not None else QAAction.noop(),
        ray_count=ray_count,
    )


def test_schema_version_and_default_flat_size_are_stable() -> None:
    assert OBSERVATION_SCHEMA_VERSION == 1
    assert DEFAULT_RAY_COUNT == 16
    assert get_flat_observation_size() == 119


@pytest.mark.parametrize("ray_count", [1, 2, 5, DEFAULT_RAY_COUNT, 32])
def test_flat_size_matches_the_configured_fixed_ray_count(ray_count: int) -> None:
    flattened = flatten_observation(make_observation(ray_count))
    assert isinstance(flattened, tuple)
    assert all(isinstance(value, float) for value in flattened)
    assert len(flattened) == get_flat_observation_size(ray_count) == 7 + 7 * ray_count


@pytest.mark.parametrize("ray_count", [0, -1, True, 1.5, "16"])
def test_flat_size_rejects_invalid_schema_ray_counts(ray_count: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        get_flat_observation_size(ray_count)


def test_flattening_has_exact_stable_player_action_and_one_hot_ray_order() -> None:
    observation = QAObservation(
        health_01=0.75,
        powerup_count=2,
        previous_action=QAAction(0.25, -0.5, True, False, 3),
        rays=(
            RayObservation(False, 1.0, QAObjectType.NONE),
            RayObservation(True, 0.1, QAObjectType.OBSTACLE),
            RayObservation(True, 0.2, QAObjectType.ENEMY),
            RayObservation(True, 0.3, QAObjectType.LOOT),
            RayObservation(True, 0.4, QAObjectType.EXIT),
        ),
        ray_count=5,
    )
    expected = (
        0.75, 2.0, 0.25, -0.5, 1.0, 0.0, 3.0,
        0.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0,
        1.0, 0.1, 0.0, 1.0, 0.0, 0.0, 0.0,
        1.0, 0.2, 0.0, 0.0, 1.0, 0.0, 0.0,
        1.0, 0.3, 0.0, 0.0, 0.0, 1.0, 0.0,
        1.0, 0.4, 0.0, 0.0, 0.0, 0.0, 1.0,
    )
    assert flatten_observation(observation) == expected
    assert flatten_observation(observation) == expected
    assert flatten_observation(QAObservation.from_dict(observation.to_dict())) == expected


def test_flat_size_stays_fixed_when_action_flags_and_ability_change() -> None:
    actions = [QAAction.noop(), QAAction(1.0, -1.0, True, True, 100)]
    for action in actions:
        flattened = flatten_observation(make_observation(DEFAULT_RAY_COUNT, action))
        assert len(flattened) == 119


def test_powerup_count_and_ability_are_raw_floats_in_the_flat_contract() -> None:
    observation = QAObservation(
        0.5,
        8,
        (RayObservation(False, 1.0, QAObjectType.NONE),),
        QAAction(ability=9),
        ray_count=1,
    )
    flattened = flatten_observation(observation)
    assert flattened[1] == 8.0
    assert flattened[6] == 9.0
