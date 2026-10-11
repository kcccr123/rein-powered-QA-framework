"""Verify the optional shared RL interface using only engine-free environments."""

from dataclasses import replace

import pytest

gym = pytest.importorskip("gymnasium")
np = pytest.importorskip("numpy")

from qa_framework.environments.fake import FakeQAEnvironment
from qa_framework.environments.gymnasium import GymnasiumQAEnvironment
from qa_framework.evaluation.result import EpisodeResult, TerminalReason
from qa_framework.models.action import QAAction
from qa_framework.models.episode import EpisodeConfig
from qa_framework.models.level import LevelSpec
from qa_framework.schema.validation import flatten_observation


class RecordingEnvironment(FakeQAEnvironment):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.reset_calls = []
        self.step_calls = []
        self.close_calls = 0
        self.returned_info = None
        self.schema_version = 1

    @property
    def observation_schema_version(self) -> int:
        return self.schema_version

    def reset(self, *, seed=None, config=None):
        self.reset_calls.append((seed, config))
        observation, info = super().reset(seed=seed, config=config)
        self.returned_info = info
        return observation, info

    def step(self, action):
        self.step_calls.append(action)
        result = super().step(action)
        self.returned_info = result[4]
        return result

    def close(self):
        self.close_calls += 1
        super().close()


def test_construction_declares_spaces_without_starting_an_episode(monkeypatch) -> None:
    import socket
    import subprocess

    def forbidden(*args, **kwargs):
        pytest.fail("offline RL wrapper attempted to connect or launch a process")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    inner = RecordingEnvironment()
    wrapper = GymnasiumQAEnvironment(inner)
    assert isinstance(wrapper, gym.Env)
    assert wrapper.action_space.shape == (5,)
    assert wrapper.action_space.dtype == np.float32
    assert np.all(wrapper.action_space.low == -1)
    assert np.all(wrapper.action_space.high == 1)
    assert wrapper.observation_space.shape == (119,)
    assert wrapper.observation_space.dtype == np.float32
    assert not inner.episode_active
    assert inner.reset_calls == []
    observation, info = wrapper.reset(seed=7)
    assert wrapper.observation_space.contains(observation)
    wrapper.step(np.asarray([0, 0, -1, -1, -1], dtype=np.float32))
    wrapper.close()
    assert inner.reset_calls == [(7, None)]
    assert inner.step_calls == [QAAction.noop()]


def test_flat_observations_and_terminal_results_preserve_qa_contract() -> None:
    inner = RecordingEnvironment(completion_steps=1)
    reference = FakeQAEnvironment(completion_steps=1)
    wrapper = GymnasiumQAEnvironment(inner, max_ability_slot=4)
    expected, _ = reference.reset(seed=42)
    observed, info = wrapper.reset(seed=42)
    np.testing.assert_array_equal(observed, np.asarray(flatten_observation(expected), dtype=np.float32))
    assert info is inner.returned_info
    action = np.asarray([0.5, -0.25, 0.1, 0, 0.5], dtype=np.float32)
    observed, reward, terminated, truncated, info = wrapper.step(action)
    assert inner.step_calls == [QAAction(0.5, -0.25, True, False, 3)]
    assert (reward, terminated, truncated) == (1.0, True, False)
    assert info is inner.returned_info
    assert isinstance(info["episode_result"], EpisodeResult)
    assert info["episode_result"].terminal_reason is TerminalReason.COMPLETED
    assert info["episode_result"].metrics is inner.last_metrics
    np.testing.assert_array_equal(observed[2:7], [0.5, -0.25, 1, 0, 3])
    assert wrapper.observation_space.contains(observed)
    with pytest.raises(RuntimeError):
        wrapper.step(action)


def test_default_config_and_reset_override_are_forwarded_without_sticking() -> None:
    inner = RecordingEnvironment(completion_steps=20)
    default = EpisodeConfig(max_steps=3)
    override = EpisodeConfig(max_steps=1, metadata={"candidate": "level-7"})
    wrapper = GymnasiumQAEnvironment(inner, default_config=default)
    wrapper.reset(seed=12, options={"config": override})
    _, _, terminated, truncated, info = wrapper.step([0, 0, -1, -1, -1])
    assert (terminated, truncated) == (False, True)
    assert info["episode_result"].terminal_reason is TerminalReason.MAX_STEPS
    assert inner.reset_calls[-1] == (12, override)
    wrapper.reset()
    assert inner.reset_calls[-1] == (None, default)
    wrapper.step([0, 0, -1, -1, -1])
    assert inner.episode_active


def test_level_recipe_is_forwarded_and_acknowledged_without_generating_it() -> None:
    level = LevelSpec("candidate-7", 42, {"RoomCount": 12, "Seed": 42})
    config = EpisodeConfig(max_steps=1, level=level)
    inner = RecordingEnvironment()
    wrapper = GymnasiumQAEnvironment(inner)
    _, info = wrapper.reset(seed=123, options={"config": config})
    assert inner.reset_calls[-1] == (123, config)
    assert inner.reset_calls[-1][1].level is level
    assert info["level_id"] == "candidate-7"
    assert info["generation_seed"] == 42
    _, _, _, _, terminal_info = wrapper.step([0, 0, -1, -1, -1])
    assert terminal_info["level_id"] == "candidate-7"
    assert terminal_info["generation_seed"] == 42
    assert terminal_info["episode_result"].metrics.step_count == 1


def test_gymnasium_checker_accepts_the_fake_numerical_contract() -> None:
    from gymnasium.utils.env_checker import check_env

    class DeterministicInfoEnvironment(FakeQAEnvironment):
        def _make_info(self):
            info = super()._make_info()
            # The checker compares all info after repeating a seed, while QA
            # deliberately gives every episode a new diagnostic identifier.
            info.pop("episode_id")
            return info

    wrapper = GymnasiumQAEnvironment(DeterministicInfoEnvironment())
    try:
        with pytest.warns(UserWarning, match="maximum value is infinity"):
            check_env(wrapper, skip_render_check=True)
    finally:
        wrapper.close()


@pytest.mark.parametrize(
    "options", [{"seed": 1}, {"config": {"max_steps": 1}}, {"config": None}, [], {1: "unknown"}]
)
def test_invalid_reset_options_do_not_advance_or_reset_the_inner_environment(options: object) -> None:
    inner = RecordingEnvironment()
    wrapper = GymnasiumQAEnvironment(inner)
    wrapper.reset(seed=8)
    wrapper.step([0, 0, -1, -1, -1])
    metrics = inner.last_metrics
    with pytest.raises((TypeError, ValueError)):
        wrapper.reset(options=options)
    assert inner.reset_calls == [(8, None)]
    assert inner.last_metrics is metrics


@pytest.mark.parametrize("seed", [True, -1, 1.5, "123"])
def test_invalid_reset_seed_does_not_reach_the_inner_environment(seed: object) -> None:
    inner = RecordingEnvironment()
    wrapper = GymnasiumQAEnvironment(inner)
    with pytest.raises((TypeError, ValueError)):
        wrapper.reset(seed=seed)
    assert inner.reset_calls == []


@pytest.mark.parametrize(
    "action",
    [
        np.zeros((1, 5)), np.zeros(4), np.zeros(6), np.zeros((5, 1)),
        np.asarray([0, 0, np.nan, -1, -1]), np.asarray([0, 0, -1, -1, np.inf]),
        np.asarray([0, 0, -1, -1, 1.01]), np.ones(5, dtype=bool),
        [0, 0, True, -1, -1], [0, 0, np.bool_(True), -1, -1], "00000", None,
    ],
)
def test_invalid_actions_are_rejected_before_the_inner_environment_steps(action: object) -> None:
    inner = RecordingEnvironment()
    wrapper = GymnasiumQAEnvironment(inner)
    wrapper.reset(seed=7)
    metrics = inner.last_metrics
    with pytest.raises((TypeError, ValueError)):
        wrapper.step(action)
    assert inner.step_calls == []
    assert inner.last_metrics is metrics


def test_numpy_scalar_sequence_is_normalized_before_qa_validation() -> None:
    inner = RecordingEnvironment()
    wrapper = GymnasiumQAEnvironment(inner)
    wrapper.reset()
    wrapper.step([np.float32(0.5), np.int64(-1), np.float16(1), np.float64(-1), np.int32(1)])
    assert inner.step_calls == [QAAction(0.5, -1.0, True, False, 0)]
    assert type(inner.step_calls[0].move_x) is float


def test_ray_count_mismatch_is_rejected_and_custom_ray_count_is_supported() -> None:
    with pytest.raises(ValueError, match="16 rays"):
        GymnasiumQAEnvironment(FakeQAEnvironment(ray_count=3)).reset()
    wrapper = GymnasiumQAEnvironment(FakeQAEnvironment(ray_count=3), ray_count=3)
    observation, _ = wrapper.reset()
    assert observation.shape == (28,)
    assert wrapper.observation_space.contains(observation)


def test_ray_count_is_checked_on_each_step() -> None:
    class ChangedRaysEnvironment(FakeQAEnvironment):
        def step(self, action):
            result = super().step(action)
            observation = replace(result[0], rays=result[0].rays[:3], ray_count=3)
            return observation, *result[1:]

    wrapper = GymnasiumQAEnvironment(ChangedRaysEnvironment())
    wrapper.reset()
    with pytest.raises(ValueError, match="16 rays"):
        wrapper.step([0, 0, -1, -1, -1])
    assert not wrapper.qa_environment.episode_active
    with pytest.raises(RuntimeError, match="closed"):
        wrapper.reset()


def test_schema_version_is_checked_before_starting_or_advancing() -> None:
    inner = RecordingEnvironment()
    inner.schema_version = 2
    with pytest.raises(ValueError, match="schema version"):
        GymnasiumQAEnvironment(inner)
    inner.schema_version = 1
    wrapper = GymnasiumQAEnvironment(inner)
    wrapper.reset()
    inner.schema_version = 2
    with pytest.raises(ValueError, match="schema version"):
        wrapper.step([0, 0, -1, -1, -1])
    with pytest.raises(ValueError, match="schema version"):
        wrapper.reset()
    assert inner.step_calls == []
    assert len(inner.reset_calls) == 1


def test_info_schema_mismatch_is_rejected() -> None:
    class IncorrectInfoEnvironment(FakeQAEnvironment):
        def reset(self, **kwargs):
            observation, info = super().reset(**kwargs)
            info["schema_version"] = 2
            return observation, info

    with pytest.raises(ValueError, match="schema version in info"):
        GymnasiumQAEnvironment(IncorrectInfoEnvironment()).reset()


@pytest.mark.parametrize("on_step", [False, True])
def test_invalid_backend_info_is_rejected_and_closes_the_session(on_step: bool) -> None:
    class InvalidInfoEnvironment(RecordingEnvironment):
        def reset(self, **kwargs):
            observation, info = super().reset(**kwargs)
            return observation, info if on_step else None

        def step(self, action):
            result = super().step(action)
            return *result[:4], None

    inner = InvalidInfoEnvironment()
    wrapper = GymnasiumQAEnvironment(inner)
    with pytest.raises(TypeError, match="info must be a dictionary"):
        wrapper.reset()
        wrapper.step([0, 0, -1, -1, -1])
    assert inner.close_calls == 1
    assert not inner.episode_active


def test_float32_overflow_retires_session_before_reaching_the_policy() -> None:
    class ExcessiveCountEnvironment(RecordingEnvironment):
        def reset(self, **kwargs):
            observation, info = super().reset(**kwargs)
            return replace(observation, powerup_count=10**50), info

    inner = ExcessiveCountEnvironment()
    with pytest.raises(ValueError, match="finite float32"):
        GymnasiumQAEnvironment(inner).reset()
    assert inner.close_calls == 1
    assert not inner.episode_active


def test_observation_error_is_preserved_if_cleanup_also_fails() -> None:
    class CleanupFailureEnvironment(RecordingEnvironment):
        def reset(self, **kwargs):
            observation, info = super().reset(**kwargs)
            return replace(observation, rays=observation.rays[:3], ray_count=3), info

        def close(self):
            super().close()
            raise RuntimeError("cleanup failed")

    inner = CleanupFailureEnvironment()
    with pytest.raises(ValueError, match="16 rays") as raised:
        GymnasiumQAEnvironment(inner).reset()
    assert isinstance(raised.value.__cause__, RuntimeError)
    assert inner.close_calls == 1


def test_close_delegates_once_and_disallows_further_use() -> None:
    inner = RecordingEnvironment()
    wrapper = GymnasiumQAEnvironment(inner)
    wrapper.close()
    wrapper.close()
    assert inner.close_calls == 1
    with pytest.raises(RuntimeError, match="closed"):
        wrapper.reset()
    with pytest.raises(RuntimeError, match="closed"):
        wrapper.step([0, 0, -1, -1, -1])
