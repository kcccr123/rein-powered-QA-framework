"""Descriptive episode metrics, independent of evaluation policy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from qa_framework.schema.json_data import copy_json_object
from qa_framework.schema.validation import (
    validate_boolean,
    validate_finite_number,
    validate_integer,
)


@dataclass(frozen=True, slots=True)
class EpisodeMetrics:
    """Immutable metric snapshot; zero defaults also mean no events recorded."""

    step_count: int = 0
    elapsed_seconds: float = 0.0
    completed: bool = False
    player_died: bool = False
    enemies_killed: int = 0
    damage_taken: float = 0.0
    powerups_used: int = 0
    stuck_events: int = 0

    def __post_init__(self) -> None:
        for name in ("step_count", "enemies_killed", "powerups_used", "stuck_events"):
            validate_integer(getattr(self, name), name=name, minimum=0)
        for name in ("elapsed_seconds", "damage_taken"):
            validate_finite_number(getattr(self, name), name=name, minimum=0.0)
        validate_boolean(self.completed, name="completed")
        validate_boolean(self.player_died, name="player_died")

    def to_dict(self) -> dict[str, object]:
        """Return descriptive metrics as finite JSON fields for a future handoff."""
        return {
            "step_count": self.step_count,
            "elapsed_seconds": float(self.elapsed_seconds),
            "completed": self.completed,
            "player_died": self.player_died,
            "enemies_killed": self.enemies_killed,
            "damage_taken": float(self.damage_taken),
            "powerups_used": self.powerups_used,
            "stuck_events": self.stuck_events,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> EpisodeMetrics:
        """Restore metrics using the same validation as direct construction."""
        # JSON validation precedes decoding; the constructor validates field types.
        payload: dict[str, Any] = copy_json_object(data, name="episode metrics data")
        return cls(
            step_count=payload["step_count"],
            elapsed_seconds=payload["elapsed_seconds"],
            completed=payload["completed"],
            player_died=payload["player_died"],
            enemies_killed=payload["enemies_killed"],
            damage_taken=payload["damage_taken"],
            powerups_used=payload["powerups_used"],
            stuck_events=payload["stuck_events"],
        )
