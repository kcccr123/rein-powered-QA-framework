"""The fake implements the public environment lifecycle without engine tooling."""

from dataclasses import FrozenInstanceError

import pytest

from qa_framework.environments.base import QAEnvironment
from qa_framework.environments.fake import FakeQAEnvironment
from qa_framework.evaluation.result import EpisodeResult, TerminalReason
from qa_framework.models.action import QAAction
from qa_framework.models.episode import EpisodeConfig
from qa_framework.models.metrics import EpisodeMetrics
from qa_framework.schema.constants import OBSERVATION_SCHEMA_VERSION


@pytest.fixture
def env() -> FakeQAEnvironment:
    return FakeQAEnvironment()


def test_fake_implements_environment_contract(env: FakeQAEnvironment) -> None:
    assert isinstance(env, QAEnvironment)
    with pytest.raises(TypeError):
        QAEnvironment()
    assert not env.episode_active
    assert env.last_metrics == EpisodeMetrics()


def test_reset_returns_noop_and_exposes_schema(env: FakeQAEnvironment) -> None:
    observation, info = env.reset(seed=123)
    assert observation.previous_action == QAAction.noop()
    assert info == {"schema_version": OBSERVATION_SCHEMA_VERSION, "step": 0, "episode_id": "fake-1"}
    assert env.observation_schema_version == OBSERVATION_SCHEMA_VERSION
    assert env.episode_active


def test_step_records_previous_action(env: FakeQAEnvironment) -> None:
    env.reset(seed=123)
    action = QAAction(move_x=1.0, move_y=-0.5, attack=True, ability=2)
    observation, reward, terminated, truncated, info = env.step(action)
    assert observation.previous_action == action
    assert (reward, terminated, truncated) == (0.0, False, False)
    assert info["step"] == 1
    assert "episode_result" not in info
    assert env.last_metrics.step_count == 1
    assert env.last_metrics.elapsed_seconds == pytest.approx(0.1)


def test_reset_clears_previous_action_and_metrics(env: FakeQAEnvironment) -> None:
    env.reset(seed=123)
    env.step(QAAction(move_x=1.0, attack=True))
    previous_metrics = env.last_metrics
    observation, info = env.reset(seed=123)
    assert observation.previous_action == QAAction.noop()
    assert env.last_metrics == EpisodeMetrics()
    assert previous_metrics.step_count == 1
    assert info["step"] == 0
    assert info["episode_id"] == "fake-2"
    assert "episode_result" not in info
    with pytest.raises(FrozenInstanceError):
        env.last_metrics.step_count = 12


def test_same_seed_and_actions_produce_identical_full_traces() -> None:
    first = FakeQAEnvironment(completion_steps=4)
    second = FakeQAEnvironment(completion_steps=4)
    assert first.reset(seed=123) == second.reset(seed=123)
    actions = (QAAction.noop(), QAAction(move_x=1.0), QAAction(attack=True), QAAction(ability=1))
    for action in actions:
        assert first.step(action) == second.step(action)
        assert first.last_metrics == second.last_metrics
    first.close()
    second.close()


def test_reseeding_repeats_observations(env: FakeQAEnvironment) -> None:
    initial, _ = env.reset(seed=-12)
    after_step, *_ = env.step(QAAction(move_y=1.0))
    repeated_initial, _ = env.reset(seed=-12)
    repeated_after_step, *_ = env.step(QAAction(move_y=1.0))
    assert initial == repeated_initial
    assert after_step == repeated_after_step


def test_episode_completes_at_configured_step() -> None:
    env = FakeQAEnvironment(completion_steps=3)
    env.reset(seed=7)
    for _ in range(2):
        _, reward, terminated, truncated, _ = env.step(QAAction.noop())
        assert (reward, terminated, truncated) == (0.0, False, False)
    _, reward, terminated, truncated, info = env.step(QAAction.noop())
    assert (reward, terminated, truncated) == (1.0, True, False)
    assert not env.episode_active
    result = info["episode_result"]
    assert isinstance(result, EpisodeResult)
    assert result.terminal_reason is TerminalReason.COMPLETED
    assert result.metrics == env.last_metrics
    assert result.metrics.completed
    assert result.metrics.step_count == 3
    assert not result.metrics.player_died


def test_default_episode_completes_after_twenty_steps(env: FakeQAEnvironment) -> None:
    env.reset(seed=123)
    for expected_step in range(1, 21):
        _, reward, terminated, truncated, info = env.step(QAAction.noop())
        assert info["step"] == expected_step
        assert not truncated
        assert terminated == (expected_step == 20)
        assert reward == float(expected_step == 20)
    assert env.last_metrics.step_count == 20
    assert env.last_metrics.completed


def test_episode_truncates_at_max_steps(env: FakeQAEnvironment) -> None:
    env.reset(config=EpisodeConfig(max_steps=2))
    env.step(QAAction.noop())
    _, reward, terminated, truncated, info = env.step(QAAction.noop())
    assert (reward, terminated, truncated) == (0.0, False, True)
    assert info["episode_result"].terminal_reason is TerminalReason.MAX_STEPS
    assert env.last_metrics.step_count == 2
    assert not env.last_metrics.completed
    assert not env.episode_active


def test_episode_truncates_at_simulated_duration() -> None:
    env = FakeQAEnvironment(step_duration_seconds=0.15)
    env.reset(config=EpisodeConfig(max_duration_seconds=0.45))
    for _ in range(2):
        _, _, terminated, truncated, _ = env.step(QAAction.noop())
        assert not terminated and not truncated
    _, reward, terminated, truncated, info = env.step(QAAction.noop())
    assert (reward, terminated, truncated) == (0.0, False, True)
    assert info["episode_result"].terminal_reason is TerminalReason.MAX_DURATION
    assert env.last_metrics.elapsed_seconds == pytest.approx(0.45)


def test_completion_takes_precedence_at_limit_ties() -> None:
    env = FakeQAEnvironment(completion_steps=2, step_duration_seconds=0.1)
    env.reset(config=EpisodeConfig(max_steps=2, max_duration_seconds=0.2))
    env.step(QAAction.noop())
    _, reward, terminated, truncated, info = env.step(QAAction.noop())
    assert (reward, terminated, truncated) == (1.0, True, False)
    assert info["episode_result"].terminal_reason is TerminalReason.COMPLETED


def test_step_limit_takes_precedence_over_duration_tie(env: FakeQAEnvironment) -> None:
    env.reset(config=EpisodeConfig(max_steps=1, max_duration_seconds=0.1))
    *_, info = env.step(QAAction.noop())
    assert info["episode_result"].terminal_reason is TerminalReason.MAX_STEPS


def test_reset_uses_default_limits_when_config_omitted(env: FakeQAEnvironment) -> None:
    env.reset(config=EpisodeConfig(max_steps=1))
    env.step(QAAction.noop())
    env.reset()
    _, _, terminated, truncated, _ = env.step(QAAction.noop())
    assert not terminated and not truncated


def test_step_requires_reset_and_active_episode(env: FakeQAEnvironment) -> None:
    with pytest.raises(RuntimeError):
        env.step(QAAction.noop())
    env.reset(config=EpisodeConfig(max_steps=1))
    env.step(QAAction.noop())
    metrics = env.last_metrics
    with pytest.raises(RuntimeError):
        env.step(QAAction.noop())
    assert env.last_metrics == metrics
    env.reset()
    assert env.episode_active


def test_invalid_action_is_rejected_before_metrics_change(env: FakeQAEnvironment) -> None:
    env.reset(seed=123)
    with pytest.raises(TypeError):
        env.step(object())
    assert env.last_metrics == EpisodeMetrics()
    assert env.episode_active
    observation, *_ = env.step(QAAction.noop())
    assert observation.previous_action == QAAction.noop()


@pytest.mark.parametrize("seed", [True, False, 1.2, "123"])
def test_invalid_seed_is_rejected_without_resetting(env: FakeQAEnvironment, seed: object) -> None:
    env.reset()
    env.step(QAAction(move_x=1.0))
    metrics = env.last_metrics
    with pytest.raises(TypeError):
        env.reset(seed=seed)
    assert env.last_metrics == metrics


def test_invalid_config_is_rejected(env: FakeQAEnvironment) -> None:
    with pytest.raises(TypeError):
        env.reset(config={"max_steps": 2})
    assert not env.episode_active


def test_explicit_ray_count_applies_to_each_observation() -> None:
    env = FakeQAEnvironment(ray_count=3)
    observation, _ = env.reset(seed=3)
    assert len(observation.rays) == 3
    observation, *_ = env.step(QAAction.noop())
    assert len(observation.rays) == 3


@pytest.mark.parametrize(
    "kwargs",
    [
        {"completion_steps": 0},
        {"completion_steps": True},
        {"ray_count": 0},
        {"ray_count": 1.5},
        {"step_duration_seconds": 0},
        {"step_duration_seconds": -1},
        {"step_duration_seconds": float("nan")},
        {"step_duration_seconds": float("inf")},
    ],
)
def test_invalid_constructor_settings_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        FakeQAEnvironment(**kwargs)


def test_close_is_safe_and_prevents_further_use(env: FakeQAEnvironment) -> None:
    env.reset()
    env.step(QAAction.noop())
    metrics = env.last_metrics
    env.close()
    env.close()
    assert not env.episode_active
    assert env.last_metrics == metrics
    with pytest.raises(RuntimeError):
        env.step(QAAction.noop())
    with pytest.raises(RuntimeError):
        env.reset()


def test_close_before_reset_is_safe(env: FakeQAEnvironment) -> None:
    env.close()
    env.close()
    assert env.last_metrics == EpisodeMetrics()
    assert not env.episode_active
