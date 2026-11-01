"""Deprecated gameplay validation and observation encoding retained for reference."""

from __future__ import annotations

from typing import TYPE_CHECKING

from qa_framework.schema.constants import (
    DEFAULT_RAY_COUNT,
    FLAT_ACTION_SIZE,
    FLAT_PLAYER_STATE_SIZE,
    FLAT_RAY_SIZE,
    OBJECT_TYPE_ENCODING_ORDER,
)
from qa_framework.schema.primitives import (
    validate_boolean,
    validate_finite_number,
    validate_integer,
)

if TYPE_CHECKING:
    from qa_framework.models.action import QAAction
    from qa_framework.models.observation import QAObservation
    from qa_framework.models.perception import RayObservation


def validate_action(action: QAAction, *, max_ability_slot: int | None = None) -> None:
    """Validate the action contract, optionally limiting available ability slots."""
    from qa_framework.models.action import QAAction

    if not isinstance(action, QAAction):
        raise TypeError("action must be a QAAction")
    validate_finite_number(action.move_x, name="move_x", minimum=-1, maximum=1)
    validate_finite_number(action.move_y, name="move_y", minimum=-1, maximum=1)
    validate_boolean(action.attack, name="attack")
    validate_boolean(action.interact, name="interact")
    validate_integer(action.ability, name="ability")
    if max_ability_slot is not None:
        validate_integer(max_ability_slot, name="max_ability_slot")
        if action.ability > max_ability_slot:
            raise ValueError(f"ability must be <= {max_ability_slot}")


def validate_ray(ray: RayObservation) -> None:
    """Validate a classified or unclassified fixed-length ray reading."""
    from qa_framework.models.perception import QAObjectType, RayObservation

    if not isinstance(ray, RayObservation):
        raise TypeError("ray must be a RayObservation")
    validate_boolean(ray.hit, name="hit")
    validate_finite_number(ray.distance_01, name="distance_01", minimum=0, maximum=1)
    if not isinstance(ray.object_type, QAObjectType):
        raise TypeError("object_type must be a QAObjectType")
    if not ray.hit and ray.object_type is not QAObjectType.NONE:
        raise ValueError("a ray without a hit must have object_type NONE")


def validate_observation(
    observation: QAObservation, *, ray_count: int | None = None
) -> None:
    """Validate state and its declared ray count, optionally enforcing an adapter schema."""
    from qa_framework.models.observation import QAObservation

    if not isinstance(observation, QAObservation):
        raise TypeError("observation must be a QAObservation")
    validate_finite_number(observation.health_01, name="health_01", minimum=0, maximum=1)
    validate_integer(observation.powerup_count, name="powerup_count")
    validate_integer(observation.ray_count, name="ray_count", minimum=1)
    if ray_count is not None:
        validate_integer(ray_count, name="ray_count", minimum=1)
        if observation.ray_count != ray_count:
            raise ValueError(f"observation schema requires {ray_count} rays")
    if not isinstance(observation.rays, tuple):
        raise TypeError("rays must be a tuple of RayObservation objects")
    if len(observation.rays) != observation.ray_count:
        raise ValueError(f"expected exactly {observation.ray_count} rays")
    for ray in observation.rays:
        validate_ray(ray)
    validate_action(observation.previous_action)


def get_flat_observation_size(ray_count: int = DEFAULT_RAY_COUNT) -> int:
    """Return schema v1's fixed size: two state values, five action values, seven per ray."""
    validate_integer(ray_count, name="ray_count", minimum=1)
    return FLAT_PLAYER_STATE_SIZE + FLAT_ACTION_SIZE + ray_count * FLAT_RAY_SIZE


def flatten_observation(observation: QAObservation) -> tuple[float, ...]:
    """Encode state, previous action, then ordered ray hit/distance/type one-hot values."""
    validate_observation(observation)
    action = observation.previous_action
    values = [
        float(observation.health_01),
        float(observation.powerup_count),
        float(action.move_x),
        float(action.move_y),
        float(action.attack),
        float(action.interact),
        float(action.ability),
    ]
    for ray in observation.rays:
        values.extend((float(ray.hit), float(ray.distance_01)))
        values.extend(
            float(ray.object_type.value == name) for name in OBJECT_TYPE_ENCODING_ORDER
        )
    return tuple(values)
