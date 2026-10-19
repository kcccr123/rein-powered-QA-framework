"""Complete episodes feed a bounded next recipe, independently of transport/RL."""

import json
from dataclasses import replace
from itertools import islice

import pytest

from qa_framework import EpisodeConfig, FakeQAEnvironment, LevelSpec, QAAction
from qa_framework.environments.base import QAEnvironmentError
from qa_framework.evaluation.result import EpisodeResult, TerminalReason
from qa_framework.orchestration import (
    AdaptiveDifficultyConfig, AdaptiveState, RoundReport, iter_adaptive_rounds,
)


def baseline(seed=42):
    return LevelSpec("candidate", seed, {
        "RoomCount": 10, "AreaWidth": 100, "AreaHeight": 100,
        "MinRoomWidth": 5, "MaxRoomWidth": 10,
        "MinRoomHeight": 5, "MaxRoomHeight": 10,
    }, generator_version="test-generator-v1")


def noop(_):
    return QAAction.noop()


def test_full_episode_drives_next_recipe_and_preserves_provenance():
    env = FakeQAEnvironment(completion_steps=2)
    reports = list(iter_adaptive_rounds(
        env, noop, baseline(), rounds=3, config=EpisodeConfig(max_steps=100),
        qa_seed=800, policy_id="checkpoint-1", run_id="qa-run",
    ))
    first, second, third = reports
    assert first.evaluation.request.level == baseline()
    assert first.evaluation.result.metrics.step_count == 2
    assert first.state_after.skill > first.state_before.skill
    assert first.state_after.difficulty > first.state_before.difficulty
    assert second.evaluation.request.level == first.next_level
    assert third.evaluation.request.level == second.next_level
    assert second.state_before == first.state_after
    assert first.next_level.generation_parameters["RoomCount"] > 10
    assert first.next_level.generation_parameters["AreaWidth"] > 100
    assert first.next_level.generation_parameters["AreaHeight"] > 100
    assert [r.evaluation.request.qa_seed for r in reports] == [800, 801, 802]
    assert [r.evaluation.request.level.generation_seed for r in reports] == [42, 43, 44]
    assert [r.evaluation.request.request_id for r in reports] == ["qa-run-round-1", "qa-run-round-2", "qa-run-round-3"]
    assert all(r.evaluation.request.policy_id == "checkpoint-1" for r in reports)
    assert all(r.next_level.generator_version == "test-generator-v1" for r in reports)
    for report in reports:
        for field in ("MinRoomWidth", "MaxRoomWidth", "MinRoomHeight", "MaxRoomHeight"):
            assert report.next_level.generation_parameters[field] == baseline().generation_parameters[field]
    env.close()


def test_adaptation_and_geometry_stay_within_bounds_over_many_rounds():
    settings = AdaptiveDifficultyConfig(max_difficulty_change=0.05)
    reports = list(iter_adaptive_rounds(
        FakeQAEnvironment(completion_steps=1), noop, baseline(), rounds=100,
        config=EpisodeConfig(max_steps=100), adaptation=settings,
    ))
    assert reports[-1].state_after.difficulty > 0.99
    assert reports[-1].next_level.generation_parameters["RoomCount"] == 14
    for report in reports:
        assert 0 <= report.state_after.skill <= 1
        assert 0 <= report.state_after.difficulty <= 1
        assert abs(report.state_after.difficulty - report.state_before.difficulty) <= 0.05 + 1e-12
        recipe = report.next_level.generation_parameters
        assert 6 <= recipe["RoomCount"] <= 14
        assert 80 <= recipe["AreaWidth"] <= 120
        assert 80 <= recipe["AreaHeight"] <= 120


@pytest.mark.parametrize("config,expected_reason", [
    (EpisodeConfig(max_steps=2), TerminalReason.MAX_STEPS),
    (EpisodeConfig(max_steps=100, max_duration_seconds=0.2), TerminalReason.MAX_DURATION),
])
def test_budget_truncation_is_incomplete_and_lowers_next_difficulty(config, expected_reason):
    report = next(iter_adaptive_rounds(FakeQAEnvironment(completion_steps=20), noop, baseline(), config=config))
    assert report.evaluation.result.terminal_reason is expected_reason
    assert report.evaluation.result.truncated
    assert report.skill_signal == 0
    assert report.state_after.skill < report.state_before.skill
    assert report.state_after.difficulty < report.state_before.difficulty
    assert report.next_level.generation_parameters["RoomCount"] < 10
    assert report.next_level.generation_parameters["AreaWidth"] < 100


def test_death_with_positive_reward_still_lowers_difficulty():
    class Death(FakeQAEnvironment):
        def step(self, action):
            observation, _, _, _, info = super().step(action)
            self._metrics = replace(self.last_metrics, completed=False, player_died=True)
            self._episode_active = False
            info["episode_result"] = EpisodeResult(True, False, TerminalReason.PLAYER_DIED, self._metrics)
            return observation, 99.0, True, False, info

    report = next(iter_adaptive_rounds(Death(), noop, baseline()))
    assert report.evaluation.total_reward == 99
    assert report.skill_signal == 0
    assert report.state_after.difficulty < 0.5


@pytest.mark.parametrize("damage,stuck", [(10.0, 0), (0.0, 5), (10.0, 5)])
def test_completed_damage_and_stuck_metrics_lower_signal_and_next_geometry(damage, stuck):
    class CompletedWithMetrics(FakeQAEnvironment):
        def step(self, action):
            observation, reward, terminated, truncated, info = super().step(action)
            self._metrics = replace(self.last_metrics, damage_taken=damage, stuck_events=stuck)
            info["episode_result"] = EpisodeResult(True, False, TerminalReason.COMPLETED, self._metrics)
            return observation, reward, terminated, truncated, info

    config = EpisodeConfig(max_steps=100)
    clean = next(iter_adaptive_rounds(FakeQAEnvironment(completion_steps=1), noop, baseline(), config=config))
    affected = next(iter_adaptive_rounds(CompletedWithMetrics(completion_steps=1), noop, baseline(), config=config))
    assert affected.evaluation.result.terminal_reason is TerminalReason.COMPLETED
    assert affected.skill_signal < clean.skill_signal
    assert affected.state_after.skill < clean.state_after.skill
    assert affected.state_after.difficulty < clean.state_after.difficulty
    assert affected.next_level.generation_parameters["AreaWidth"] < clean.next_level.generation_parameters["AreaWidth"]


def test_penalties_use_explicit_normalization_and_remain_bounded():
    class CostlyCompletion(FakeQAEnvironment):
        def step(self, action):
            observation, reward, terminated, truncated, info = super().step(action)
            self._metrics = replace(self.last_metrics, damage_taken=1_000, stuck_events=1_000)
            info["episode_result"] = EpisodeResult(True, False, TerminalReason.COMPLETED, self._metrics)
            return observation, reward, terminated, truncated, info

    settings = AdaptiveDifficultyConfig(damage_penalty_weight=1, stuck_penalty_weight=1)
    report = next(iter_adaptive_rounds(CostlyCompletion(completion_steps=1), noop, baseline(), adaptation=settings))
    assert report.skill_signal == 0
    no_penalties = replace(settings, damage_penalty_weight=0, stuck_penalty_weight=0)
    report = next(iter_adaptive_rounds(CostlyCompletion(completion_steps=1), noop, baseline(), adaptation=no_penalties))
    assert report.skill_signal > 0.99


def test_infrastructure_failure_holds_calibration_and_stops_retired_session():
    class Failure(FakeQAEnvironment):
        def step(self, action):
            if self.last_metrics.step_count == 1:
                raise QAEnvironmentError("TCP disconnected")
            return super().step(action)

    env = Failure()
    reports = list(iter_adaptive_rounds(env, noop, baseline(), rounds=None))
    assert len(reports) == 1
    report = reports[0]
    assert report.evaluation.result.terminal_reason is TerminalReason.ENVIRONMENT_FAILURE
    assert report.evaluation.result.metrics.step_count == 1
    assert report.evaluation.error == "TCP disconnected"
    assert report.skill_signal is None and report.next_level is None
    assert report.state_after.skill == report.state_before.skill
    assert report.state_after.difficulty == report.state_before.difficulty
    with pytest.raises(RuntimeError, match="closed"):
        env.reset()
    assert RoundReport.from_dict(json.loads(json.dumps(report.to_dict(), allow_nan=False))) == report


def test_later_reset_failure_does_not_apply_previous_success_twice():
    class ResetFailure(FakeQAEnvironment):
        def reset(self, **kwargs):
            if self._episode_number == 1:
                raise QAEnvironmentError("generation failed")
            return super().reset(**kwargs)

    reports = list(iter_adaptive_rounds(ResetFailure(completion_steps=1), noop, baseline(), rounds=5))
    assert len(reports) == 2
    assert reports[1].evaluation.result.metrics.step_count == 0
    assert reports[1].state_before == reports[0].state_after
    assert reports[1].state_after.skill == reports[0].state_after.skill
    assert reports[1].next_level is None


def test_indefinite_iterator_is_lazy_and_resets_policy_between_complete_episodes():
    class Policy:
        def __init__(self):
            self.episode = 0
            self.calls = 0

        def reset_episode(self):
            self.episode += 1
            self.calls = 0

        def __call__(self, observation):
            self.calls += 1
            assert self.calls <= 2
            return QAAction.noop()

    env, policy = FakeQAEnvironment(completion_steps=2), Policy()
    rounds = iter_adaptive_rounds(env, policy, baseline(), rounds=None)
    assert policy.episode == 0
    assert not env.episode_active
    reports = list(islice(rounds, 3))
    assert len(reports) == 3 and policy.episode == 3 and policy.calls == 2
    assert not env.episode_active
    rounds.close()
    env.close()


def test_round_json_and_state_resume_preserve_next_request():
    first = next(iter_adaptive_rounds(FakeQAEnvironment(completion_steps=1), noop, baseline(), qa_seed=8))
    restored = RoundReport.from_dict(json.loads(json.dumps(first.to_dict(), allow_nan=False)))
    assert restored == first
    resumed = next(iter_adaptive_rounds(
        FakeQAEnvironment(completion_steps=1), noop, restored.baseline_level,
        initial_state=restored.state_after, adaptation=restored.adaptation, qa_seed=8,
    ))
    assert resumed.evaluation.request.level == restored.next_level
    assert resumed.evaluation.request.qa_seed == 9
    assert resumed.round_number == 2
    assert resumed.state_before == restored.state_after


def test_seed_wrap_is_valid_for_indefinite_runs():
    reports = list(iter_adaptive_rounds(
        FakeQAEnvironment(completion_steps=1), noop, baseline(seed=2**31 - 1),
        rounds=2, qa_seed=2**31 - 1,
    ))
    assert reports[1].evaluation.request.level.generation_seed == 0
    assert reports[1].evaluation.request.level.generation_parameters["Seed"] == 0
    assert reports[1].evaluation.request.qa_seed == 0


def test_policy_failure_propagates_without_generating_an_adaptation_report():
    def broken(_):
        raise LookupError("missing checkpoint")

    env = FakeQAEnvironment()
    with pytest.raises(LookupError, match="missing checkpoint"):
        next(iter_adaptive_rounds(env, broken, baseline(), rounds=3))
    with pytest.raises(RuntimeError, match="closed"):
        env.reset()


@pytest.mark.parametrize("field,value", [("RoomCount", True), ("MaxRoomWidth", 90), ("AreaWidth", 1000)])
def test_invalid_or_out_of_bounds_geometry_is_rejected_before_reset(field, value):
    original = baseline()
    parameters = dict(original.generation_parameters)
    parameters[field] = value
    env = FakeQAEnvironment()
    with pytest.raises((TypeError, ValueError)):
        iter_adaptive_rounds(env, noop, replace(original, generation_parameters=parameters))
    assert env.last_metrics.step_count == 0 and not env.episode_active


def test_incomplete_recipe_is_rejected_before_first_round():
    with pytest.raises(ValueError, match="MinRoomWidth"):
        iter_adaptive_rounds(FakeQAEnvironment(), noop, LevelSpec("candidate", 0, {
            "RoomCount": 10, "AreaWidth": 100, "AreaHeight": 100,
        }))


@pytest.mark.parametrize("kwargs", [{"rounds": 0}, {"rounds": True}, {"qa_seed": True}, {"run_id": ""}])
def test_invalid_loop_inputs_fail_before_playing(kwargs):
    with pytest.raises((TypeError, ValueError)):
        iter_adaptive_rounds(FakeQAEnvironment(), noop, baseline(), **kwargs)


@pytest.mark.parametrize("kwargs", [
    {"initial_difficulty": 0}, {"skill_update_rate": 0},
    {"max_difficulty_change": float("nan")}, {"minimum_room_count": 15},
    {"damage_scale": 0}, {"stuck_event_scale": -1},
])
def test_invalid_calibration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        AdaptiveDifficultyConfig(**kwargs)


def test_serialized_round_cannot_convert_infrastructure_failure_into_skill_evidence():
    class Failure(FakeQAEnvironment):
        def reset(self, **kwargs):
            raise QAEnvironmentError("reset failed")

    report = next(iter_adaptive_rounds(Failure(), noop, baseline()))
    payload = report.to_dict()
    payload["skill_signal"] = 0
    with pytest.raises(ValueError, match="environment failures"):
        RoundReport.from_dict(payload)
    payload = report.to_dict()
    payload["schema_version"] = 2
    with pytest.raises(ValueError, match="schema version"):
        RoundReport.from_dict(payload)


@pytest.mark.parametrize("mutation", ["skill", "next_level", "baseline"])
def test_serialized_round_cannot_change_decision_without_matching_provenance(mutation):
    report = next(iter_adaptive_rounds(FakeQAEnvironment(completion_steps=1), noop, baseline()))
    payload = report.to_dict()
    if mutation == "skill":
        payload["state_after"]["skill"] = 0.9
    elif mutation == "next_level":
        payload["next_level"]["generation_parameters"]["RoomCount"] = 9
    else:
        payload["baseline_level"]["level_id"] = "another-candidate"
    with pytest.raises(ValueError):
        RoundReport.from_dict(payload)


def test_repeated_truncation_reaches_lower_geometry_bounds():
    report = list(iter_adaptive_rounds(
        FakeQAEnvironment(completion_steps=100), noop, baseline(), rounds=100,
        config=EpisodeConfig(max_steps=1),
    ))[-1]
    assert report.state_after.difficulty < 1e-10
    assert report.next_level.generation_parameters["RoomCount"] == 6
    assert report.next_level.generation_parameters["AreaWidth"] == 80
    assert report.next_level.generation_parameters["AreaHeight"] == 80
