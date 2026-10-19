"""A lazy outer loop over complete QA episodes, without training or PCG imports."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, fields, replace
from typing import Any

from qa_framework.environments.base import QAEnvironment
from qa_framework.evaluation.result import TerminalReason
from qa_framework.evaluation.runner import (
    EvaluationReport,
    EvaluationRequest,
    QAPolicy,
    evaluate_episode,
)
from qa_framework.models.episode import EpisodeConfig
from qa_framework.models.level import LevelSpec
from qa_framework.schema.json_data import copy_json_object
from qa_framework.schema.validation import validate_finite_number, validate_integer

ORCHESTRATION_SCHEMA_VERSION = 1
_SEED_MODULUS = 2**31
_RECIPE_FIELDS = (
    "RoomCount", "AreaWidth", "AreaHeight", "MinRoomWidth", "MaxRoomWidth",
    "MinRoomHeight", "MaxRoomHeight",
)


@dataclass(frozen=True, slots=True)
class AdaptiveDifficultyConfig:
    """Heuristic calibration, not a learned or validated player-skill model.

    Difficulty interpolates room count and area dimensions between explicit
    bounds, anchored to the original recipe at ``initial_difficulty``. Room
    dimensions and other supplied settings remain fixed. Larger geometry is an
    assumed increase in navigation demand; gameplay difficulty is not guaranteed.
    Bounds must be chosen for the Unity project's fixed generation settings.

    Completion efficiency loses up to ``damage_penalty_weight`` for cumulative
    damage / ``damage_scale`` and ``stuck_penalty_weight`` for stuck events /
    ``stuck_event_scale``. Each ratio is capped at one. Damage uses the raw
    metric's health units, not health_01; the default scale matches 10 health
    units. Unreported combat/stuck metrics retain the adapter's zero defaults.
    """

    initial_difficulty: float = 0.5
    initial_skill: float = 0.5
    skill_update_rate: float = 0.25
    max_difficulty_change: float = 0.1
    damage_penalty_weight: float = 0.25
    damage_scale: float = 10.0
    stuck_penalty_weight: float = 0.25
    stuck_event_scale: float = 5.0
    minimum_room_count: int = 6
    maximum_room_count: int = 14
    minimum_area_width: int = 80
    maximum_area_width: int = 120
    minimum_area_height: int = 80
    maximum_area_height: int = 120

    def __post_init__(self) -> None:
        for name in (
            "initial_difficulty", "initial_skill", "skill_update_rate", "max_difficulty_change",
            "damage_penalty_weight", "stuck_penalty_weight",
        ):
            validate_finite_number(getattr(self, name), name=name, minimum=0, maximum=1)
        if not 0 < self.initial_difficulty < 1:
            raise ValueError("initial_difficulty must be strictly between zero and one")
        for name in ("skill_update_rate", "max_difficulty_change"):
            if getattr(self, name) == 0:
                raise ValueError(f"{name} must be greater than zero")
        for name in ("damage_scale", "stuck_event_scale"):
            validate_finite_number(getattr(self, name), name=name, minimum=0)
            if getattr(self, name) == 0:
                raise ValueError(f"{name} must be greater than zero")
        for dimension in ("room_count", "area_width", "area_height"):
            lower, upper = f"minimum_{dimension}", f"maximum_{dimension}"
            validate_integer(getattr(self, lower), name=lower, minimum=2)
            validate_integer(getattr(self, upper), name=upper, minimum=2)
            if getattr(self, lower) >= getattr(self, upper):
                raise ValueError(f"{lower} must be less than {upper}")

    def to_dict(self) -> dict[str, object]:
        return {item.name: getattr(self, item.name) for item in fields(self)}

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> AdaptiveDifficultyConfig:
        return cls(**copy_json_object(data, name="adaptation"))


@dataclass(frozen=True, slots=True)
class AdaptiveState:
    """Bounded controller state; rounds_completed includes interrupted attempts."""

    difficulty: float = 0.5
    skill: float = 0.5
    rounds_completed: int = 0

    def __post_init__(self) -> None:
        for name in ("difficulty", "skill"):
            validate_finite_number(getattr(self, name), name=name, minimum=0, maximum=1)
        validate_integer(self.rounds_completed, name="rounds_completed")

    def to_dict(self) -> dict[str, object]:
        return {
            "difficulty": float(self.difficulty),
            "skill": float(self.skill),
            "rounds_completed": self.rounds_completed,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> AdaptiveState:
        return cls(**copy_json_object(data, name="adaptive state"))


@dataclass(frozen=True, slots=True)
class RoundReport:
    """One replayable evaluation plus the decision that feeds the next round.

    A null signal/next_level means infrastructure failed: calibration was held
    and the closed environment must be replaced before any further playthrough.
    A next recipe is prepared even on the final requested round, for inspection
    or resumption with its state and the same baseline/configuration/seeds.
    """

    evaluation: EvaluationReport
    state_before: AdaptiveState
    state_after: AdaptiveState
    skill_signal: float | None
    next_level: LevelSpec | None
    baseline_level: LevelSpec
    adaptation: AdaptiveDifficultyConfig

    def __post_init__(self) -> None:
        for name, expected in (
            ("evaluation", EvaluationReport), ("state_before", AdaptiveState),
            ("state_after", AdaptiveState), ("baseline_level", LevelSpec),
            ("adaptation", AdaptiveDifficultyConfig),
        ):
            if not isinstance(getattr(self, name), expected):
                raise TypeError(f"{name} must be {expected.__name__}")
        if self.state_after.rounds_completed != self.state_before.rounds_completed + 1:
            raise ValueError("a round must advance rounds_completed exactly once")
        if self.next_level is not None and not isinstance(self.next_level, LevelSpec):
            raise TypeError("next_level must be LevelSpec or None")
        failure = self.evaluation.result.terminal_reason is TerminalReason.ENVIRONMENT_FAILURE
        if failure:
            if self.skill_signal is not None or self.next_level is not None:
                raise ValueError("environment failures cannot supply skill evidence or a next level")
            if (self.state_after.skill, self.state_after.difficulty) != (
                self.state_before.skill, self.state_before.difficulty
            ):
                raise ValueError("environment failures must hold skill and difficulty")
        else:
            validate_finite_number(self.skill_signal, name="skill_signal", minimum=0, maximum=1)
            if self.next_level is None:
                raise ValueError("gameplay outcomes require a next level recipe")
        _validate_recipe(self.baseline_level, self.adaptation)
        if self.evaluation.request.level != _current_level(self.baseline_level, self.adaptation, self.state_before):
            raise ValueError("evaluated level must match the baseline and prior controller state")
        expected_signal = _skill_signal(self.evaluation, self.adaptation)
        if self.skill_signal != expected_signal:
            raise ValueError("skill_signal must agree with the evaluation and calibration")
        if self.state_after != _advance_state(self.state_before, expected_signal, self.adaptation):
            raise ValueError("state_after must agree with the evaluation and calibration")
        if not failure and self.next_level != _next_level(self.baseline_level, self.adaptation, self.state_after):
            raise ValueError("next_level must match the updated controller state")

    @property
    def round_number(self) -> int:
        return self.state_after.rounds_completed

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": ORCHESTRATION_SCHEMA_VERSION,
            "evaluation": self.evaluation.to_dict(),
            "state_before": self.state_before.to_dict(),
            "state_after": self.state_after.to_dict(),
            "skill_signal": self.skill_signal,
            "next_level": None if self.next_level is None else self.next_level.to_dict(),
            "baseline_level": self.baseline_level.to_dict(),
            "adaptation": self.adaptation.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> RoundReport:
        payload: dict[str, Any] = copy_json_object(data, name="round report")
        validate_integer(payload["schema_version"], name="schema_version", minimum=1)
        if payload["schema_version"] != ORCHESTRATION_SCHEMA_VERSION:
            raise ValueError("unsupported orchestration schema version")
        return cls(
            evaluation=EvaluationReport.from_dict(payload["evaluation"]),
            state_before=AdaptiveState.from_dict(payload["state_before"]),
            state_after=AdaptiveState.from_dict(payload["state_after"]),
            skill_signal=payload["skill_signal"],
            next_level=None if payload["next_level"] is None else LevelSpec.from_dict(payload["next_level"]),
            baseline_level=LevelSpec.from_dict(payload["baseline_level"]),
            adaptation=AdaptiveDifficultyConfig.from_dict(payload["adaptation"]),
        )


def iter_adaptive_rounds(
    environment: QAEnvironment,
    policy: QAPolicy,
    initial_level: LevelSpec,
    *,
    rounds: int | None = 1,
    config: EpisodeConfig | None = None,
    adaptation: AdaptiveDifficultyConfig | None = None,
    initial_state: AdaptiveState | None = None,
    qa_seed: int = 0,
    policy_id: str | None = None,
    run_id: str = "adaptive",
) -> Iterator[RoundReport]:
    """Yield N episodes (None means indefinite) with constant memory usage.

    A policy's optional ``reset_episode()`` hook runs before each evaluation.
    Completed episodes start at 0.5..1 according to budget efficiency, then lose
    configurable normalized damage/stuck penalties and are clamped to [0,1].
    Death and step/duration truncation score zero. An exponential average updates the
    heuristic skill estimate, then difficulty moves toward it by at most the
    configured change. Raw reward never determines success. Infrastructure
    failure yields unchanged calibration once and stops; policy/contract errors
    propagate. The caller owns and closes a successful environment.

    The initial recipe must contain Unity's seven supported geometry fields.
    On resumption, pass the original baseline, configuration, seeds and run_id
    together with the last report's state_after. Every yield is a full round;
    no connection or rollout starts until the iterator is advanced.
    """
    if not isinstance(environment, QAEnvironment):
        raise TypeError("environment must implement QAEnvironment")
    if not callable(policy):
        raise TypeError("policy must be callable")
    if not isinstance(initial_level, LevelSpec):
        raise TypeError("initial_level must be LevelSpec")
    if rounds is not None:
        validate_integer(rounds, name="rounds", minimum=1)
    adaptation = AdaptiveDifficultyConfig() if adaptation is None else adaptation
    if not isinstance(adaptation, AdaptiveDifficultyConfig):
        raise TypeError("adaptation must be AdaptiveDifficultyConfig")
    state = initial_state if initial_state is not None else AdaptiveState(
        difficulty=adaptation.initial_difficulty, skill=adaptation.initial_skill,
    )
    if not isinstance(state, AdaptiveState):
        raise TypeError("initial_state must be AdaptiveState")
    config = EpisodeConfig() if config is None else config
    if not isinstance(config, EpisodeConfig):
        raise TypeError("config must be EpisodeConfig")
    if config.level is not None and config.level != initial_level:
        raise ValueError("config.level must match initial_level")
    validate_integer(qa_seed, name="qa_seed")
    if qa_seed >= _SEED_MODULUS:
        raise ValueError("qa_seed must fit a nonnegative 32-bit integer")
    for name, value in (("run_id", run_id), ("policy_id", policy_id)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{name} must be a nonempty string")
    if run_id is None:
        raise TypeError("run_id must be a string")
    _validate_recipe(initial_level, adaptation)

    def run() -> Iterator[RoundReport]:
        current = state
        remaining = rounds
        while remaining is None or remaining > 0:
            index = current.rounds_completed
            level = _current_level(initial_level, adaptation, current)
            reset_policy = getattr(policy, "reset_episode", None)
            if reset_policy is not None:
                if not callable(reset_policy):
                    raise TypeError("policy.reset_episode must be callable")
                reset_policy()
            request = EvaluationRequest(
                request_id=f"{run_id}-round-{index + 1}", level=level,
                qa_seed=(qa_seed + index) % _SEED_MODULUS,
                config=replace(config, level=level), policy_id=policy_id,
            )
            evaluation = evaluate_episode(environment, policy, request)
            signal = _skill_signal(evaluation, adaptation)
            after = _advance_state(current, signal, adaptation)
            next_level = None if signal is None else _next_level(initial_level, adaptation, after)
            yield RoundReport(evaluation, current, after, signal, next_level, initial_level, adaptation)
            if signal is None:
                return
            current = after
            if remaining is not None:
                remaining -= 1

    return run()


def _advance_state(
    current: AdaptiveState, signal: float | None, config: AdaptiveDifficultyConfig,
) -> AdaptiveState:
    after = replace(current, rounds_completed=current.rounds_completed + 1)
    if signal is None:
        return after
    skill = _clamp(current.skill + config.skill_update_rate * (signal - current.skill))
    change = max(-config.max_difficulty_change, min(
        config.max_difficulty_change, skill - current.difficulty,
    ))
    return replace(after, skill=skill, difficulty=_clamp(current.difficulty + change))


def _skill_signal(report: EvaluationReport, adaptation: AdaptiveDifficultyConfig) -> float | None:
    reason = report.result.terminal_reason
    if reason is TerminalReason.ENVIRONMENT_FAILURE:
        return None
    if reason is not TerminalReason.COMPLETED:
        return 0.0
    metrics, config = report.result.metrics, report.request.config
    fraction = metrics.step_count / config.max_steps
    if config.max_duration_seconds is not None:
        fraction = max(fraction, metrics.elapsed_seconds / config.max_duration_seconds)
    damage_penalty = adaptation.damage_penalty_weight * _clamp(metrics.damage_taken / adaptation.damage_scale)
    stuck_penalty = adaptation.stuck_penalty_weight * _clamp(metrics.stuck_events / adaptation.stuck_event_scale)
    return _clamp(1.0 - 0.5 * _clamp(fraction) - damage_penalty - stuck_penalty)


def _validate_recipe(level: LevelSpec, config: AdaptiveDifficultyConfig) -> None:
    parameters = level.generation_parameters
    for name in _RECIPE_FIELDS:
        if name not in parameters:
            raise ValueError(f"initial_level requires generation parameter {name}")
        validate_integer(parameters[name], name=name, minimum=2)
    for axis in ("Width", "Height"):
        if parameters[f"MinRoom{axis}"] > parameters[f"MaxRoom{axis}"]:
            raise ValueError(f"MinRoom{axis} must not exceed MaxRoom{axis}")
        if getattr(config, f"minimum_area_{axis.lower()}") <= parameters[f"MaxRoom{axis}"] + 2:
            raise ValueError(f"minimum_area_{axis.lower()} must leave room for room walls")
    for name, dimension in (("RoomCount", "room_count"), ("AreaWidth", "area_width"), ("AreaHeight", "area_height")):
        if not getattr(config, f"minimum_{dimension}") <= parameters[name] <= getattr(config, f"maximum_{dimension}"):
            raise ValueError(f"initial_level {name} must lie within adaptation bounds")


def _current_level(baseline: LevelSpec, config: AdaptiveDifficultyConfig, state: AdaptiveState) -> LevelSpec:
    if state.rounds_completed == 0 and state.difficulty == config.initial_difficulty:
        return baseline
    return _next_level(baseline, config, state)


def _next_level(baseline: LevelSpec, config: AdaptiveDifficultyConfig, state: AdaptiveState) -> LevelSpec:
    parameters = baseline.pcg_parameters()
    for name, dimension in (("RoomCount", "room_count"), ("AreaWidth", "area_width"), ("AreaHeight", "area_height")):
        base = parameters[name]
        if state.difficulty >= config.initial_difficulty:
            fraction = (state.difficulty - config.initial_difficulty) / (1 - config.initial_difficulty)
            value = base + fraction * (getattr(config, f"maximum_{dimension}") - base)
        else:
            fraction = (config.initial_difficulty - state.difficulty) / config.initial_difficulty
            value = base - fraction * (base - getattr(config, f"minimum_{dimension}"))
        parameters[name] = int(round(value))
    seed = (baseline.generation_seed + state.rounds_completed) % _SEED_MODULUS
    parameters["Seed"] = seed
    return LevelSpec(
        level_id=f"{baseline.level_id}-round-{state.rounds_completed + 1}",
        generation_seed=seed, generation_parameters=parameters,
        generator_version=baseline.generator_version,
    )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
