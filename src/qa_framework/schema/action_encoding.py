"""A normalized action contract for shared RL policies, without ML dependencies."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import floor
from typing import TYPE_CHECKING

from qa_framework.schema.constants import FLAT_ACTION_SIZE
from qa_framework.schema.validation import (
    validate_action,
    validate_finite_number,
    validate_integer,
)

if TYPE_CHECKING:
    from qa_framework.models.action import QAAction


ACTION_ENCODING_VERSION = 1


@dataclass(frozen=True, slots=True)
class QAActionCodec:
    """Encode move_x, move_y, attack, interact, ability into five values in [-1, 1].

    Movement keeps its per-axis values. Positive button values press a button;
    zero and negative values release it. Encoding uses -1 for released buttons
    and 1 for pressed buttons. Ability slots 0..max_ability_slot are evenly
    spaced from -1 to 1; decoding chooses the nearest slot, with exact ties
    choosing the higher slot. Slot zero means no ability. With no configured
    abilities, every valid fifth component decodes to zero, and encodes as -1.

    This contract describes policy outputs. A future backend must explicitly
    map these QA commands onto the game's supported controls.
    """

    max_ability_slot: int = 0

    def __post_init__(self) -> None:
        validate_integer(self.max_ability_slot, name="max_ability_slot")

    @property
    def encoding_version(self) -> int:
        return ACTION_ENCODING_VERSION

    @property
    def action_size(self) -> int:
        return FLAT_ACTION_SIZE

    def encode(self, action: QAAction) -> tuple[float, float, float, float, float]:
        """Return the canonical normalized representation of a supported action."""
        validate_action(action, max_ability_slot=self.max_ability_slot)
        ability = (
            2.0 * (action.ability / self.max_ability_slot) - 1.0
            if self.max_ability_slot
            else -1.0
        )
        return (
            float(action.move_x),
            float(action.move_y),
            1.0 if action.attack else -1.0,
            1.0 if action.interact else -1.0,
            ability,
        )

    def decode(self, values: Sequence[float]) -> QAAction:
        """Validate a regular numeric sequence before decoding any command.

        Values must be finite Python int/float numbers; booleans are rejected.
        The optional Gymnasium wrapper also accepts NumPy numeric arrays.
        """
        from qa_framework.models.action import QAAction

        if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
            raise TypeError("encoded action must be a numeric sequence")
        if len(values) != self.action_size:
            raise ValueError("encoded action must contain exactly five values")
        for index, value in enumerate(values):
            validate_finite_number(value, name=f"action[{index}]", minimum=-1, maximum=1)
        move_x, move_y, attack, interact, ability = (float(value) for value in values)
        slot = (
            floor(((ability + 1.0) / 2.0) * self.max_ability_slot + 0.5)
            if self.max_ability_slot
            else 0
        )
        return QAAction(move_x, move_y, attack > 0.0, interact > 0.0, slot)
