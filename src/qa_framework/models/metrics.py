"""Descriptive episode metrics, independent of evaluation policy."""

from dataclasses import dataclass

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
