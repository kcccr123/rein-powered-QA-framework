"""Immutable schema v1 observations and JSON-compatible serialization."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from qa_framework.models.action import QAAction
from qa_framework.models.perception import QAObjectType, RayObservation
from qa_framework.schema.constants import DEFAULT_RAY_COUNT, OBSERVATION_SCHEMA_VERSION
from qa_framework.schema.validation import validate_integer, validate_observation


@dataclass(frozen=True, slots=True)
class QAObservation:
    """Player state, ordered rays, and the last applied action.

    Supply ray_count explicitly for a different fixed-size schema. Input list/tuple
    rays are copied to a tuple so mutating a producer's container cannot change state.
    """

    health_01: float
    powerup_count: int
    rays: tuple[RayObservation, ...]
    previous_action: QAAction = field(default_factory=QAAction.noop)
    ray_count: int = field(default=DEFAULT_RAY_COUNT, kw_only=True)

    def __post_init__(self) -> None:
        if not isinstance(self.rays, (list, tuple)):
            raise TypeError("rays must be a list or tuple of RayObservation objects")
        object.__setattr__(self, "rays", tuple(self.rays))
        validate_observation(self)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible payload carrying its schema version and ray count."""
        return {
            "schema_version": OBSERVATION_SCHEMA_VERSION,
            "ray_count": self.ray_count,
            "health_01": float(self.health_01),
            "powerup_count": self.powerup_count,
            "previous_action": self.previous_action.to_dict(),
            "rays": [
                {
                    "hit": ray.hit,
                    "distance_01": float(ray.distance_01),
                    "object_type": ray.object_type.value,
                }
                for ray in self.rays
            ],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> QAObservation:
        """Reconstruct a versioned observation; unknown schema versions are rejected."""
        if not isinstance(data, Mapping):
            raise TypeError("observation data must be a mapping")
        version = data["schema_version"]
        validate_integer(version, name="schema_version", minimum=1)
        if version != OBSERVATION_SCHEMA_VERSION:
            raise ValueError(f"unsupported observation schema version: {version}")
        ray_data = data["rays"]
        if not isinstance(ray_data, (list, tuple)):
            raise TypeError("serialized rays must be a list or tuple")
        rays = []
        for item in ray_data:
            if not isinstance(item, Mapping):
                raise TypeError("each serialized ray must be a mapping")
            rays.append(
                RayObservation(
                    hit=item["hit"],
                    distance_01=item["distance_01"],
                    object_type=QAObjectType(item["object_type"]),
                )
            )
        return cls(
            health_01=data["health_01"],
            powerup_count=data["powerup_count"],
            rays=tuple(rays),
            previous_action=QAAction.from_dict(data["previous_action"]),
            ray_count=data["ray_count"],
        )
