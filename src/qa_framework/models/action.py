"""Transport-independent commands for a top-down QA agent."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from qa_framework.schema.validation import validate_action


@dataclass(frozen=True, slots=True)
class QAAction:
    """Movement is bounded per axis; physical/diagonal semantics belong to the environment."""

    move_x: float = 0.0
    move_y: float = 0.0
    attack: bool = False
    interact: bool = False
    ability: int = 0

    def __post_init__(self) -> None:
        validate_action(self)

    @classmethod
    def noop(cls) -> QAAction:
        """Return the action used before the first step of an episode."""
        return cls()

    def to_dict(self) -> dict[str, float | bool | int]:
        """Return a JSON-compatible action with named fields."""
        return {
            "move_x": float(self.move_x),
            "move_y": float(self.move_y),
            "attack": self.attack,
            "interact": self.interact,
            "ability": self.ability,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> QAAction:
        """Reconstruct and validate a serialized action without coercing invalid types."""
        if not isinstance(data, Mapping):
            raise TypeError("action data must be a mapping")
        return cls(
            move_x=data["move_x"],
            move_y=data["move_y"],
            attack=data["attack"],
            interact=data["interact"],
            ability=data["ability"],
        )


def action_to_tuple(action: QAAction) -> tuple[float, float, bool, bool, int]:
    """Encode move_x, move_y, attack, interact, ability; preserve discrete field types."""
    validate_action(action)
    return (
        float(action.move_x),
        float(action.move_y),
        action.attack,
        action.interact,
        action.ability,
    )


def action_from_tuple(values: tuple[float, float, bool, bool, int]) -> QAAction:
    """Decode the five-field primitive action representation and validate it."""
    if not isinstance(values, tuple):
        raise TypeError("encoded action must be a tuple")
    if len(values) != 5:
        raise ValueError("encoded action must contain exactly five values")
    return QAAction(*values)
