"""Observation validation, ray semantics, and portable serialization."""

from dataclasses import FrozenInstanceError
import json

import pytest

from qa_framework import (
    DEFAULT_RAY_COUNT,
    OBSERVATION_SCHEMA_VERSION,
    QAAction,
    QAObjectType,
    QAObservation,
    RayObservation,
    validate_observation,
)


def empty_rays(count: int = DEFAULT_RAY_COUNT) -> tuple[RayObservation, ...]:
    return tuple(RayObservation(False, 1.0, QAObjectType.NONE) for _ in range(count))


def test_observation_has_the_default_fixed_ray_count() -> None:
    observation = QAObservation(1.0, 0, empty_rays())
    validate_observation(observation)
    assert len(observation.rays) == DEFAULT_RAY_COUNT == 16
    assert observation.previous_action == QAAction.noop()


@pytest.mark.parametrize("health", [-0.01, 1.01, float("nan"), float("inf"), -float("inf")])
def test_invalid_health_is_rejected(health: float) -> None:
    with pytest.raises(ValueError):
        QAObservation(health, 0, empty_rays())


@pytest.mark.parametrize("health", [0.0, 1.0])
def test_health_range_includes_its_endpoints(health: float) -> None:
    assert QAObservation(health, 0, empty_rays()).health_01 == health


@pytest.mark.parametrize("health", [True, "0.5", None])
def test_health_requires_a_numeric_value(health: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        QAObservation(health, 0, empty_rays())


@pytest.mark.parametrize("count", [-1, 1.5, True, "2", float("nan"), float("inf")])
def test_invalid_powerup_count_is_rejected(count: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        QAObservation(1.0, count, empty_rays())


@pytest.mark.parametrize("distance", [-0.01, 1.01, float("nan"), float("inf"), -float("inf")])
def test_invalid_ray_distance_is_rejected(distance: float) -> None:
    with pytest.raises(ValueError):
        RayObservation(True, distance, QAObjectType.ENEMY)


@pytest.mark.parametrize("distance", [True, "0.5", None])
def test_ray_distance_requires_a_numeric_value(distance: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        RayObservation(True, distance, QAObjectType.ENEMY)


@pytest.mark.parametrize("distance", [0.0, 1.0])
def test_ray_distance_range_includes_its_endpoints(distance: float) -> None:
    assert RayObservation(True, distance, QAObjectType.OBSTACLE).distance_01 == distance


@pytest.mark.parametrize("hit", [0, 1, "true", None])
def test_ray_hit_requires_a_boolean(hit: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        RayObservation(hit, 1.0, QAObjectType.NONE)


@pytest.mark.parametrize("kind", ["ENEMY", 2, None])
def test_ray_object_type_requires_the_semantic_enum(kind: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        RayObservation(True, 0.5, kind)


@pytest.mark.parametrize("kind", [QAObjectType.OBSTACLE, QAObjectType.ENEMY, QAObjectType.LOOT, QAObjectType.EXIT])
def test_a_missed_ray_cannot_report_a_classified_object(kind: QAObjectType) -> None:
    with pytest.raises(ValueError):
        RayObservation(False, 1.0, kind)


def test_none_can_represent_a_miss_or_an_unclassified_physical_hit() -> None:
    miss = RayObservation(False, 1.0, QAObjectType.NONE)
    unclassified_hit = RayObservation(True, 0.25, QAObjectType.NONE)
    assert miss.hit is False
    assert unclassified_hit.hit is True
    # The contract permits shortened sensing ranges without prescribing physics.
    assert RayObservation(False, 0.5, QAObjectType.NONE).distance_01 == 0.5


@pytest.mark.parametrize("count", [0, DEFAULT_RAY_COUNT - 1, DEFAULT_RAY_COUNT + 1])
def test_wrong_ray_count_is_rejected(count: int) -> None:
    with pytest.raises(ValueError):
        QAObservation(1.0, 0, empty_rays(count))


def test_explicit_custom_ray_count_is_validated_against_environment_schema() -> None:
    observation = QAObservation(1.0, 0, empty_rays(2), ray_count=2)
    validate_observation(observation)
    validate_observation(observation, ray_count=2)
    with pytest.raises(ValueError):
        validate_observation(observation, ray_count=DEFAULT_RAY_COUNT)


@pytest.mark.parametrize("count", [0, -1, True, 1.5])
def test_schema_ray_count_must_be_a_positive_integer(count: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        QAObservation(1.0, 0, empty_rays(), ray_count=count)


def test_observation_requires_ray_models_and_an_action_model() -> None:
    with pytest.raises((TypeError, ValueError)):
        QAObservation(1.0, 0, (None,) * DEFAULT_RAY_COUNT)
    with pytest.raises((TypeError, ValueError)):
        QAObservation(1.0, 0, empty_rays(), previous_action=None)


def test_observation_and_rays_are_immutable_with_defensive_ray_copy() -> None:
    supplied_rays = list(empty_rays())
    observation = QAObservation(1.0, 0, supplied_rays)
    supplied_rays.clear()
    assert isinstance(observation.rays, tuple)
    assert len(observation.rays) == DEFAULT_RAY_COUNT
    with pytest.raises(FrozenInstanceError):
        observation.health_01 = 0.5
    with pytest.raises(FrozenInstanceError):
        observation.rays[0].distance_01 = 0.5


def test_observation_json_round_trip_preserves_schema_and_previous_action() -> None:
    observation = QAObservation(
        0.75,
        3,
        (RayObservation(True, 0.4, QAObjectType.ENEMY), *empty_rays(DEFAULT_RAY_COUNT - 1)),
        QAAction(0.5, -1.0, True, False, 2),
    )
    encoded = observation.to_dict()
    assert encoded["schema_version"] == OBSERVATION_SCHEMA_VERSION
    assert encoded["rays"][0]["object_type"] == "ENEMY"
    assert QAObservation.from_dict(json.loads(json.dumps(encoded))) == observation


def test_custom_schema_ray_count_survives_serialization() -> None:
    observation = QAObservation(0.5, 1, empty_rays(2), ray_count=2)
    assert QAObservation.from_dict(observation.to_dict()) == observation


def test_unsupported_serialized_schema_version_is_rejected() -> None:
    encoded = QAObservation(1.0, 0, empty_rays()).to_dict()
    encoded["schema_version"] = OBSERVATION_SCHEMA_VERSION + 1
    with pytest.raises(ValueError):
        QAObservation.from_dict(encoded)


def test_serialized_invalid_ray_data_is_rejected() -> None:
    encoded = QAObservation(1.0, 0, empty_rays()).to_dict()
    encoded["rays"][0]["distance_01"] = float("inf")
    with pytest.raises(ValueError):
        QAObservation.from_dict(encoded)


def test_unknown_serialized_object_type_is_rejected() -> None:
    encoded = QAObservation(1.0, 0, empty_rays()).to_dict()
    encoded["rays"][0]["object_type"] = "BOSS"
    with pytest.raises(ValueError):
        QAObservation.from_dict(encoded)
