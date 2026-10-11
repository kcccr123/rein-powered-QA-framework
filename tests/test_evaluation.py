"""Candidate correlation and explicit outcomes, without a live game or policy."""

import json
from dataclasses import replace

import pytest

from qa_framework import EpisodeConfig, FakeQAEnvironment, QAAction
from qa_framework.environments.base import QAEnvironmentError
from qa_framework.evaluation.result import EpisodeResult, TerminalReason
from qa_framework.evaluation.runner import (
    EvaluationReport, EvaluationRequest, QAContractError, evaluate_episode,
)
from qa_framework.models.level import LevelSpec
from qa_framework.models.metrics import EpisodeMetrics


def request(*, max_steps=3, level_id="candidate-7", qa_seed=99):
    return EvaluationRequest(
        "evaluation-1", LevelSpec(level_id, 7, {"RoomCount": 5}),
        qa_seed=qa_seed, config=EpisodeConfig(max_steps=max_steps), policy_id="qa-checkpoint-4",
    )


def test_offline_rollout_preserves_level_policy_seeds_and_final_metrics():
    candidate = request()
    env = FakeQAEnvironment(completion_steps=3)
    report = evaluate_episode(env, lambda observation: QAAction(move_x=1), candidate)
    assert report.request.config.level == candidate.level
    assert report.request.qa_seed == 99
    assert report.request.level.generation_seed == 7
    assert report.request.policy_id == "qa-checkpoint-4"
    assert report.result.terminal_reason is TerminalReason.COMPLETED
    assert report.result.metrics == env.last_metrics
    assert report.result.metrics.step_count == 3
    assert report.total_reward == 1
    assert report.environment_name == "FakeQAEnvironment"
    assert EvaluationReport.from_dict(json.loads(json.dumps(report.to_dict(), allow_nan=False))) == report
    env.close()


def test_step_and_duration_limits_report_truncation_without_completion():
    for config, reason in (
        (EpisodeConfig(max_steps=2), TerminalReason.MAX_STEPS),
        (EpisodeConfig(max_steps=10, max_duration_seconds=0.2), TerminalReason.MAX_DURATION),
    ):
        env = FakeQAEnvironment(completion_steps=20)
        report = evaluate_episode(env, lambda _: QAAction.noop(), replace(request(), config=config))
        assert report.result.truncated and not report.result.terminated
        assert report.result.terminal_reason is reason
        assert not report.result.metrics.completed
        assert report.total_reward == 0
        env.close()


def test_same_candidate_can_be_evaluated_repeatedly_with_independent_requests():
    env = FakeQAEnvironment(completion_steps=2)
    candidate = request()
    first = evaluate_episode(env, lambda _: QAAction.noop(), candidate)
    second = evaluate_episode(env, lambda _: QAAction.noop(), replace(candidate, request_id="evaluation-2"))
    assert first.result == second.result
    assert first.request.request_id != second.request.request_id
    assert first.request.level == second.request.level
    env.close()


class BrokenEnvironment(FakeQAEnvironment):
    def step(self, action):
        if self.last_metrics.step_count == 1:
            raise QAEnvironmentError("transport deadline exceeded")
        return super().step(action)


def test_adapter_failure_keeps_last_confirmed_metrics_and_retires_session():
    env = BrokenEnvironment()
    report = evaluate_episode(env, lambda _: QAAction.noop(), request())
    assert report.result.terminal_reason is TerminalReason.ENVIRONMENT_FAILURE
    assert report.result.truncated
    assert report.error == "transport deadline exceeded"
    assert report.result.metrics.step_count == 1
    with pytest.raises(RuntimeError, match="closed"):
        env.reset()


def test_reset_failure_does_not_reuse_previous_candidate_metrics():
    class BrokenReset(BrokenEnvironment):
        def reset(self, **kwargs):
            raise QAEnvironmentError("requested candidate could not be loaded")

    env = BrokenReset()
    env._metrics = EpisodeMetrics(step_count=9)
    report = evaluate_episode(env, lambda _: QAAction.noop(), request())
    assert report.result.metrics == EpisodeMetrics()
    assert not report.result.metrics.completed


def test_policy_error_propagates_instead_of_becoming_level_failure():
    def policy(_):
        raise LookupError("checkpoint problem")

    env = FakeQAEnvironment()
    with pytest.raises(LookupError, match="checkpoint problem"):
        evaluate_episode(env, policy, request())
    assert not env.episode_active


@pytest.mark.parametrize("mutation", ["missing_result", "completion_metric", "wrong_level", "wrong_seed"])
def test_adapter_contract_errors_never_return_success(mutation):
    class InvalidAdapter(FakeQAEnvironment):
        def step(self, action):
            observation, reward, terminated, truncated, info = super().step(action)
            if mutation == "missing_result":
                info.pop("episode_result")
            elif mutation == "completion_metric":
                self._metrics = replace(self.last_metrics, completed=False)
                info["episode_result"] = EpisodeResult(True, False, TerminalReason.COMPLETED, self._metrics)
            elif mutation == "wrong_level":
                info["level_id"] = "other-candidate"
            else:
                info["generation_seed"] = 8
            return observation, reward, terminated, truncated, info

    env = InvalidAdapter(completion_steps=1)
    with pytest.raises(QAContractError):
        evaluate_episode(env, lambda _: QAAction.noop(), request())
    assert not env.episode_active


def test_legacy_reset_without_level_acknowledgement_is_rejected():
    class LegacyAdapter(FakeQAEnvironment):
        def reset(self, **kwargs):
            observation, info = super().reset(**kwargs)
            info.pop("level_id")
            return observation, info

    with pytest.raises(QAContractError, match="level_id"):
        evaluate_episode(LegacyAdapter(), lambda _: QAAction.noop(), request())


def test_ignored_step_limit_cannot_create_unbounded_rollout():
    class IgnoresLimit(FakeQAEnvironment):
        def reset(self, *, seed=None, config=None):
            return super().reset(seed=seed, config=replace(config, max_steps=1000))

    with pytest.raises(QAContractError, match="max_steps"):
        evaluate_episode(IgnoresLimit(), lambda _: QAAction.noop(), request(max_steps=2))


def test_request_rejects_ambiguous_level_and_bool_seed():
    with pytest.raises(ValueError, match="config.level"):
        replace(request(), config=EpisodeConfig(level=LevelSpec("other", 8, {})))
    with pytest.raises(TypeError):
        replace(request(), qa_seed=True)


def test_report_rejects_unknown_version_and_inconsistent_completion():
    env = FakeQAEnvironment(completion_steps=1)
    report = evaluate_episode(env, lambda _: QAAction.noop(), request())
    payload = report.to_dict()
    payload["schema_version"] = 2
    with pytest.raises(ValueError, match="schema version"):
        EvaluationReport.from_dict(payload)
    with pytest.raises(QAContractError, match="completion"):
        replace(report, result=replace(report.result, metrics=EpisodeMetrics(step_count=1)))
    env.close()


def test_player_death_with_positive_reward_is_not_completion():
    class PlayerDeath(FakeQAEnvironment):
        def step(self, action):
            observation, _, _, _, info = super().step(action)
            self._metrics = replace(self.last_metrics, completed=False, player_died=True)
            self._episode_active = False
            info["episode_result"] = EpisodeResult(True, False, TerminalReason.PLAYER_DIED, self._metrics)
            return observation, 10.0, True, False, info

    report = evaluate_episode(PlayerDeath(), lambda _: QAAction.noop(), request())
    assert report.result.terminal_reason is TerminalReason.PLAYER_DIED
    assert report.result.metrics.player_died
    assert not report.result.metrics.completed
    assert report.total_reward == 10


@pytest.mark.parametrize("seed", [True, 1.0])
def test_level_seed_acknowledgement_rejects_equal_values_of_wrong_type(seed):
    class InvalidSeed(FakeQAEnvironment):
        def reset(self, **kwargs):
            observation, info = super().reset(**kwargs)
            info["generation_seed"] = seed
            return observation, info

    with pytest.raises(QAContractError, match="generation_seed"):
        candidate = EvaluationRequest("evaluation-1", LevelSpec("candidate-1", 1, {}))
        evaluate_episode(InvalidSeed(), lambda _: QAAction.noop(), candidate)


def test_reset_error_is_preserved_if_cleanup_also_fails():
    class InvalidReset(FakeQAEnvironment):
        def reset(self, **kwargs):
            super().reset(**kwargs)
            raise ValueError("bad reset payload")

        def close(self):
            super().close()
            raise OSError("close also failed")

    env = InvalidReset()
    with pytest.raises(ValueError, match="bad reset payload") as caught:
        evaluate_episode(env, lambda _: QAAction.noop(), request())
    assert isinstance(caught.value.__cause__, OSError)
    assert not env.episode_active


def test_blank_infrastructure_error_and_cleanup_failure_still_produce_feedback():
    class Broken(FakeQAEnvironment):
        def reset(self, **kwargs):
            raise QAEnvironmentError(" ")

        def close(self):
            super().close()
            raise OSError("cleanup error")

    report = evaluate_episode(Broken(), lambda _: QAAction.noop(), request())
    assert report.result.terminal_reason is TerminalReason.ENVIRONMENT_FAILURE
    assert report.error.startswith("QAEnvironmentError; close failed: OSError")


@pytest.mark.parametrize("reason,metrics", [
    (TerminalReason.MAX_STEPS, EpisodeMetrics(step_count=1)),
    (TerminalReason.MAX_DURATION, EpisodeMetrics(step_count=1, elapsed_seconds=5)),
    (TerminalReason.COMPLETED, EpisodeMetrics(step_count=4, completed=True)),
])
def test_serialized_report_cannot_contradict_requested_limits(reason, metrics):
    env = FakeQAEnvironment(completion_steps=1)
    report = evaluate_episode(env, lambda _: QAAction.noop(), request())
    payload = report.to_dict()
    result = EpisodeResult(reason is TerminalReason.COMPLETED, reason is not TerminalReason.COMPLETED, reason, metrics)
    payload["result"] = result.to_dict()
    with pytest.raises(QAContractError):
        EvaluationReport.from_dict(payload)
    env.close()


def test_ignored_duration_limit_is_rejected_on_first_overdue_step():
    class IgnoresDuration(FakeQAEnvironment):
        def reset(self, *, seed=None, config=None):
            return super().reset(seed=seed, config=replace(config, max_duration_seconds=None))

    candidate = replace(request(), config=EpisodeConfig(max_duration_seconds=0.1))
    env = IgnoresDuration()
    with pytest.raises(QAContractError, match="max_duration_seconds"):
        evaluate_episode(env, lambda _: QAAction.noop(), candidate)
    assert env.last_metrics.step_count == 1
