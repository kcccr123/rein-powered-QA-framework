"""The transport-independent action contract and primitive encoding."""

from dataclasses import FrozenInstanceError
import json

import pytest

from qa_framework import (
    QAAction,
    action_from_tuple,
    action_to_tuple,
    validate_action,
)


def test_noop_is_valid_and_has_no_effect_flags() -> None:
    action = QAAction.noop()
    validate_action(action)
    assert action == QAAction(0.0, 0.0, False, False, 0)
    assert action_to_tuple(action) == (0.0, 0.0, False, False, 0)


@pytest.mark.parametrize("field", ["move_x", "move_y"])
@pytest.mark.parametrize("value", [-1.01, 1.01, float("nan"), float("inf"), -float("inf")])
def test_invalid_movement_is_rejected(field: str, value: float) -> None:
    with pytest.raises(ValueError):
        QAAction(**{field: value})


@pytest.mark.parametrize("field", ["move_x", "move_y"])
@pytest.mark.parametrize("value", [True, "0.5", None])
def test_movement_requires_a_number_not_a_bool(field: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        QAAction(**{field: value})


def test_full_diagonal_input_is_preserved() -> None:
    # Movement physics belong to the eventual environment implementation.
    action = QAAction(move_x=1.0, move_y=-1.0)
    assert (action.move_x, action.move_y) == (1.0, -1.0)


@pytest.mark.parametrize("field", ["attack", "interact"])
@pytest.mark.parametrize("value", [0, 1, "yes", None])
def test_action_flags_require_booleans(field: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        QAAction(**{field: value})


@pytest.mark.parametrize("value", [-1, 1.5, True, "1", float("nan"), float("inf")])
def test_ability_requires_a_nonnegative_integer(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        QAAction(ability=value)


def test_optional_ability_limit_is_enforced_without_a_global_limit() -> None:
    action = QAAction(ability=100)
    validate_action(action)
    validate_action(action, max_ability_slot=100)
    with pytest.raises(ValueError):
        validate_action(action, max_ability_slot=99)


def test_action_is_immutable() -> None:
    action = QAAction.noop()
    with pytest.raises(FrozenInstanceError):
        action.move_x = 0.5


def test_primitive_action_encodings_round_trip() -> None:
    action = QAAction(0.25, -0.5, True, False, 2)
    assert action_to_tuple(action) == (0.25, -0.5, True, False, 2)
    assert action_from_tuple(action_to_tuple(action)) == action
    assert QAAction.from_dict(json.loads(json.dumps(action.to_dict()))) == action


@pytest.mark.parametrize(
    "encoded",
    [(), (0.0, 0.0), (0.0, 0.0, False, False, 0, 0), (2.0, 0.0, False, False, 0),
     (0.0, 0.0, 1, False, 0), (0.0, 0.0, False, False, -1)],
)
def test_action_tuple_decoding_validates_shape_and_values(encoded: tuple) -> None:
    with pytest.raises((TypeError, ValueError)):
        action_from_tuple(encoded)


def test_action_dictionary_decoding_validates_values() -> None:
    encoded = QAAction.noop().to_dict()
    encoded["move_x"] = float("nan")
    with pytest.raises(ValueError):
        QAAction.from_dict(encoded)
