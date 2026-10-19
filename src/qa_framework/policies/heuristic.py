"""A deterministic ray-based baseline for the current Unity gameplay sensors."""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, hypot, pi, sin

from qa_framework.models.action import QAAction
from qa_framework.models.observation import QAObservation
from qa_framework.models.perception import QAObjectType
from qa_framework.schema.constants import DEFAULT_RAY_COUNT
from qa_framework.schema.validation import (
    validate_finite_number,
    validate_integer,
    validate_observation,
)


@dataclass(frozen=True, slots=True)
class RayHeuristicPolicy:
    """Seek exits/loot, approach enemies, and retreat from enemies at low health.

    Matches ``QaPlayerController.Sense``: rays are world aligned, index zero
    points along world +Z (QA move_y), and increasing indices turn toward +X
    (QA move_x). Direction i is (sin(2*pi*i/N), cos(2*pi*i/N)). This convention
    must also hold for any other environment using this baseline.

    When no target is visible, choose clear space, favoring the previous heading.
    Nearby classified enemies/loot trigger attack/interact. Distances are fractions
    of the Unity sensor range; defaults of 0.1 correspond to 1.2 world units with
    the current 12-unit rays. No ability slot is assumed. This local baseline has
    no map or pathfinding and does not establish that a level is clearable.
    """

    ray_count: int = DEFAULT_RAY_COUNT
    low_health_threshold: float = 0.3
    action_distance_01: float = 0.1
    obstacle_clearance_01: float = 0.08

    def __post_init__(self) -> None:
        validate_integer(self.ray_count, name="ray_count", minimum=1)
        for name in (
            "low_health_threshold", "action_distance_01", "obstacle_clearance_01"
        ):
            validate_finite_number(getattr(self, name), name=name, minimum=0, maximum=1)

    def __call__(self, observation: QAObservation) -> QAAction:
        validate_observation(observation, ray_count=self.ray_count)
        if observation.health_01 == 0:
            return QAAction.noop()

        directions = tuple(
            (sin(2 * pi * index / self.ray_count), cos(2 * pi * index / self.ray_count))
            for index in range(self.ray_count)
        )
        enemies = [
            index for index, ray in enumerate(observation.rays)
            if ray.hit and ray.object_type is QAObjectType.ENEMY
        ]
        attack = any(
            observation.rays[index].distance_01 <= self.action_distance_01
            for index in enemies
        )
        interact = any(
            ray.hit and ray.object_type is QAObjectType.LOOT
            and ray.distance_01 <= self.action_distance_01
            for ray in observation.rays
        )
        candidates = [
            index for index, ray in enumerate(observation.rays)
            if not (
                ray.hit and ray.object_type in (QAObjectType.OBSTACLE, QAObjectType.NONE)
                and ray.distance_01 <= self.obstacle_clearance_01
            )
        ]
        if not candidates:
            return QAAction(attack=attack, interact=interact)

        retreating = observation.health_01 <= self.low_health_threshold and bool(enemies)
        if not retreating:
            for target_type in (QAObjectType.EXIT, QAObjectType.LOOT, QAObjectType.ENEMY):
                targets = [
                    index for index in candidates
                    if observation.rays[index].hit
                    and observation.rays[index].object_type is target_type
                ]
                if targets:
                    chosen = min(targets, key=lambda index: observation.rays[index].distance_01)
                    return self._action(directions[chosen], attack, interact)

        previous = observation.previous_action
        previous_length = hypot(previous.move_x, previous.move_y)
        heading = (
            (previous.move_x / previous_length, previous.move_y / previous_length)
            if previous_length else (0.0, 0.0)
        )

        def score(index: int) -> float:
            ray = observation.rays[index]
            x, y = directions[index]
            clearance = ray.distance_01 if ray.hit else 1.0
            result = clearance + 0.15 * (x * heading[0] + y * heading[1])
            if retreating:
                for enemy in enemies:
                    ex, ey = directions[enemy]
                    proximity = 1.0 - observation.rays[enemy].distance_01
                    result -= 2.0 * proximity * (x * ex + y * ey)
            return result

        chosen = max(candidates, key=score)
        return self._action(directions[chosen], attack, interact)

    @staticmethod
    def _action(direction: tuple[float, float], attack: bool, interact: bool) -> QAAction:
        # Remove floating-point residue on cardinal directions.
        x, y = (0.0 if abs(value) < 1e-12 else value for value in direction)
        return QAAction(move_x=x, move_y=y, attack=attack, interact=interact)
