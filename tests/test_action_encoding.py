"""The RL action encoding has explicit bounds, thresholds, and slot semantics."""

import pytest

from qa_framework.models.action import QAAction
from qa_framework.schema.action_encoding import ACTION_ENCODING_VERSION, QAActionCodec


def test_encoding_version_and_noop_are_explicit() -> None:
    codec = QAActionCodec()
    assert ACTION_ENCODING_VERSION == codec.encoding_version == 1
    assert codec.action_size == 5
    assert codec.encode(QAAction.noop()) == (0.0, 0.0, -1.0, -1.0, -1.0)
    assert codec.decode(codec.encode(QAAction.noop())) == QAAction.noop()


@pytest.mark.parametrize("max_slot", [0, 1, 2, 3, 4, 8])
def test_every_supported_slot_round_trips_with_movement_and_buttons(max_slot: int) -> None:
    codec = QAActionCodec(max_ability_slot=max_slot)
    for slot in range(max_slot + 1):
        for attack, interact in [(False, False), (True, False), (False, True), (True, True)]:
            action = QAAction(0.75, -0.25, attack, interact, slot)
            values = codec.encode(action)
            assert all(isinstance(value, float) and -1.0 <= value <= 1.0 for value in values)
            assert codec.decode(values) == action


def test_exact_field_order_and_even_ability_spacing() -> None:
    codec = QAActionCodec(max_ability_slot=4)
    assert codec.encode(QAAction(0.5, -1.0, True, False, 3)) == (0.5, -1.0, 1.0, -1.0, 0.5)
    assert [codec.encode(QAAction(ability=slot))[4] for slot in range(5)] == [-1, -0.5, 0, 0.5, 1]


@pytest.mark.parametrize("value,pressed", [(-1.0, False), (-0.01, False), (0.0, False), (0.01, True), (1.0, True)])
def test_button_threshold_is_strictly_positive(value: float, pressed: bool) -> None:
    action = QAActionCodec().decode([0, 0, value, value, -1])
    assert action.attack is pressed
    assert action.interact is pressed


@pytest.mark.parametrize(
    "value,slot",
    [(-1.0, 0), (-0.750001, 0), (-0.75, 1), (-0.25, 2), (0.25, 3), (0.75, 4), (1.0, 4)],
)
def test_ability_decode_uses_nearest_bucket_with_ties_up(value: float, slot: int) -> None:
    assert QAActionCodec(max_ability_slot=4).decode([0, 0, -1, -1, value]).ability == slot


@pytest.mark.parametrize("value", [-1.0, -0.5, 0.0, 0.5, 1.0])
def test_disabled_ability_ignores_any_valid_policy_value(value: float) -> None:
    assert QAActionCodec().decode([0, 0, -1, -1, value]).ability == 0


@pytest.mark.parametrize("value", [True, None, "0", float("nan"), float("inf"), -1.01, 1.01, [0]])
@pytest.mark.parametrize("index", range(5))
def test_every_component_rejects_invalid_types_and_values(value: object, index: int) -> None:
    values = [0, 0, -1, -1, -1]
    values[index] = value
    with pytest.raises((TypeError, ValueError)):
        QAActionCodec().decode(values)


@pytest.mark.parametrize("values", [None, "00000", {0: 0}, iter([0] * 5), [], [0] * 4, [0] * 6])
def test_invalid_containers_and_sizes_are_rejected(values: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        QAActionCodec().decode(values)


@pytest.mark.parametrize("max_slot", [True, -1, 1.5, "2"])
def test_invalid_ability_cap_is_rejected(max_slot: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        QAActionCodec(max_ability_slot=max_slot)


def test_encode_rejects_unsupported_ability_and_wrong_action_type() -> None:
    with pytest.raises(ValueError, match="ability"):
        QAActionCodec(max_ability_slot=2).encode(QAAction(ability=3))
    with pytest.raises(TypeError, match="QAAction"):
        QAActionCodec().encode(object())
