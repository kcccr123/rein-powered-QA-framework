"""Offline orchestration of one QA rollout; learning and PCG remain external."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from math import isclose
from typing import Any

from qa_framework.environments.base import QAEnvironment, QAEnvironmentError
from qa_framework.evaluation.result import EpisodeResult, TerminalReason
from qa_framework.models.action import QAAction
from qa_framework.models.episode import EpisodeConfig
from qa_framework.models.level import LevelSpec
from qa_framework.models.metrics import EpisodeMetrics
from qa_framework.models.observation import QAObservation
from qa_framework.schema.constants import OBSERVATION_SCHEMA_VERSION
from qa_framework.schema.validation import (
    validate_boolean,
    validate_action,
    validate_finite_number,
    validate_integer,
    validate_observation,
)

EVALUATION_SCHEMA_VERSION = 1
QAPolicy = Callable[[QAObservation], QAAction]


class QAContractError(RuntimeError):
    """An adapter violated the QA contract; its results cannot evaluate a level."""


@dataclass(frozen=True, slots=True)
class EvaluationRequest:
    """Correlate a candidate, QA seed and policy checkpoint with a bounded rollout."""

    request_id: str
    level: LevelSpec
    qa_seed: int = 0
    config: EpisodeConfig = field(default_factory=EpisodeConfig)
    policy_id: str | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.request_id, "request_id")
        if not isinstance(self.level, LevelSpec):
            raise TypeError("level must be a LevelSpec")
        validate_integer(self.qa_seed, name="qa_seed")
        if self.qa_seed > 2_147_483_647:
            raise ValueError("qa_seed must fit a nonnegative 32-bit integer")
        if not isinstance(self.config, EpisodeConfig):
            raise TypeError("config must be an EpisodeConfig")
        if self.config.level is not None and self.config.level != self.level:
            raise ValueError("config.level must match the requested level")
        if self.policy_id is not None:
            _validate_identifier(self.policy_id, "policy_id")
        object.__setattr__(self, "config", replace(self.config, level=self.level))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": EVALUATION_SCHEMA_VERSION,
            "request_id": self.request_id,
            "level": self.level.to_dict(),
            "qa_seed": self.qa_seed,
            "config": self.config.to_dict(),
            "policy_id": self.policy_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EvaluationRequest:
        _validate_envelope(data)
        return cls(
            request_id=data["request_id"],
            level=LevelSpec.from_dict(data["level"]),
            qa_seed=data["qa_seed"],
            config=EpisodeConfig.from_dict(data["config"]),
            policy_id=data["policy_id"],
        )


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Descriptive feedback for PCG; no acceptance threshold or PCG reward is chosen."""

    request: EvaluationRequest
    result: EpisodeResult
    total_reward: float
    environment_name: str
    error: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request, EvaluationRequest):
            raise TypeError("request must be an EvaluationRequest")
        if not isinstance(self.result, EpisodeResult):
            raise TypeError("result must be an EpisodeResult")
        validate_finite_number(self.total_reward, name="total_reward")
        _validate_identifier(self.environment_name, "environment_name")
        if self.error is not None:
            _validate_identifier(self.error, "error")
            if self.result.terminal_reason is not TerminalReason.ENVIRONMENT_FAILURE:
                raise ValueError("error details require an environment_failure result")
        _validate_outcome(self.result)
        _validate_requested_limits(self.request, self.result)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": EVALUATION_SCHEMA_VERSION,
            "request": self.request.to_dict(),
            "result": self.result.to_dict(),
            "total_reward": float(self.total_reward),
            "environment_name": self.environment_name,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EvaluationReport:
        _validate_envelope(data)
        return cls(
            request=EvaluationRequest.from_dict(data["request"]),
            result=EpisodeResult.from_dict(data["result"]),
            total_reward=data["total_reward"],
            environment_name=data["environment_name"],
            error=data["error"],
        )


def evaluate_episode(
    environment: QAEnvironment, policy: QAPolicy, request: EvaluationRequest
) -> EvaluationReport:
    """Run a supplied policy on a supplied environment without opening any transport.

    Only QAEnvironmentError denotes a recoverable infrastructure failure. Policy
    exceptions and invalid adapter outputs propagate instead of blaming a level.
    Failed sessions are closed; successful sessions remain caller-owned and reusable.
    The adapter must enforce duration limits and bounded individual step execution.
    """
    if not isinstance(environment, QAEnvironment):
        raise TypeError("environment must implement QAEnvironment")
    if not callable(policy):
        raise TypeError("policy must be callable")
    if not isinstance(request, EvaluationRequest):
        raise TypeError("request must be an EvaluationRequest")
    environment_name = type(environment).__name__
    total_reward = 0.0
    metrics = EpisodeMetrics()

    def failure(backend_error: QAEnvironmentError) -> EvaluationReport:
        message = str(backend_error).strip() or type(backend_error).__name__
        try:
            environment.close()
        except Exception as failure_cleanup:
            # Keep the transport failure as the outcome and preserve cleanup context.
            message += f"; close failed: {type(failure_cleanup).__name__}: {failure_cleanup}"
        return EvaluationReport(
            request=request,
            result=EpisodeResult(
                terminated=False,
                truncated=True,
                terminal_reason=TerminalReason.ENVIRONMENT_FAILURE,
                metrics=metrics,
            ),
            total_reward=total_reward,
            environment_name=environment_name,
            error=message,
        )

    try:
        _validate_environment_schema(environment)
        try:
            observation, info = environment.reset(seed=request.qa_seed, config=request.config)
        except QAEnvironmentError as error:
            return failure(error)
        _validate_environment_schema(environment)
        validate_observation(observation)
        ray_count = observation.ray_count
        _validate_context(info, request)
        if observation.previous_action != QAAction.noop():
            raise QAContractError("reset must clear previous_action")
        validate_boolean(environment.episode_active, name="episode_active")
        if environment.last_metrics != EpisodeMetrics() or not environment.episode_active:
            raise QAContractError("reset must clear metrics and start an active episode")

        for step in range(1, request.config.max_steps + 1):
            action = policy(observation)
            validate_action(action)
            _validate_environment_schema(environment)
            try:
                observation, reward, terminated, truncated, info = environment.step(action)
            except QAEnvironmentError as error:
                return failure(error)
            _validate_environment_schema(environment)
            validate_observation(observation, ray_count=ray_count)
            validate_finite_number(reward, name="reward")
            validate_boolean(terminated, name="terminated")
            validate_boolean(truncated, name="truncated")
            validate_boolean(environment.episode_active, name="episode_active")
            _validate_context(info, request)
            if observation.previous_action != action:
                raise QAContractError("previous_action must match the applied action")
            current_metrics = environment.last_metrics
            if not isinstance(current_metrics, EpisodeMetrics):
                raise QAContractError("last_metrics must be EpisodeMetrics")
            if current_metrics.step_count != step:
                raise QAContractError("metrics must count each applied QA action once")
            if current_metrics.elapsed_seconds < metrics.elapsed_seconds:
                raise QAContractError("elapsed_seconds must not decrease")
            metrics = current_metrics
            total_reward += reward
            validate_finite_number(total_reward, name="total_reward")
            if terminated or truncated:
                result = info.get("episode_result")
                if not isinstance(result, EpisodeResult):
                    raise QAContractError("ending steps require an explicit EpisodeResult")
                if (result.terminated, result.truncated) != (terminated, truncated):
                    raise QAContractError("EpisodeResult flags must match step flags")
                if result.metrics != metrics or environment.episode_active:
                    raise QAContractError("ending step and final metrics must agree")
                _validate_outcome(result)
                return EvaluationReport(request, result, total_reward, environment_name)
            if not environment.episode_active or "episode_result" in info:
                raise QAContractError("nonterminal step must retain an active episode")
            if metrics.completed or metrics.player_died:
                raise QAContractError("active episode metrics cannot report completion or death")
            duration = request.config.max_duration_seconds
            if duration is not None and _limit_reached(metrics.elapsed_seconds, duration):
                raise QAContractError("environment did not enforce the requested max_duration_seconds")
        raise QAContractError("environment did not enforce the requested max_steps")
    except Exception as error:
        # Retire a session whose state can no longer be trusted; preserve the error.
        try:
            environment.close()
        except Exception as cleanup_error:
            raise error from cleanup_error
        raise


def _validate_context(info: object, request: EvaluationRequest) -> None:
    if not isinstance(info, dict):
        raise QAContractError("environment info must be a dictionary")
    if info.get("level_id") != request.level.level_id:
        raise QAContractError("adapter must acknowledge the requested level_id")
    seed = info.get("generation_seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed != request.level.generation_seed:
        raise QAContractError("adapter must acknowledge the generation_seed")
    if "schema_version" in info:
        validate_integer(info["schema_version"], name="info schema_version", minimum=1)
        if info["schema_version"] != OBSERVATION_SCHEMA_VERSION:
            raise QAContractError("unsupported observation schema in info")


def _validate_outcome(result: EpisodeResult) -> None:
    if result.metrics.completed != (result.terminal_reason is TerminalReason.COMPLETED):
        raise QAContractError("completion requires an explicit completed outcome and metric")
    if result.metrics.player_died != (result.terminal_reason is TerminalReason.PLAYER_DIED):
        raise QAContractError("player death outcome and metric must agree")


def _validate_environment_schema(environment: QAEnvironment) -> None:
    version = environment.observation_schema_version
    validate_integer(version, name="observation_schema_version", minimum=1)
    if version != OBSERVATION_SCHEMA_VERSION:
        raise QAContractError("unsupported environment observation schema")


def _validate_requested_limits(request: EvaluationRequest, result: EpisodeResult) -> None:
    step_count = result.metrics.step_count
    if step_count > request.config.max_steps:
        raise QAContractError("result exceeds the requested max_steps")
    if step_count == 0 and result.terminal_reason is not TerminalReason.ENVIRONMENT_FAILURE:
        raise QAContractError("a gameplay outcome requires at least one applied QA step")
    if result.terminal_reason is TerminalReason.MAX_STEPS and step_count != request.config.max_steps:
        raise QAContractError("max_steps outcome must reach the requested limit")
    if result.terminal_reason is TerminalReason.MAX_DURATION:
        duration = request.config.max_duration_seconds
        if duration is None or not _limit_reached(result.metrics.elapsed_seconds, duration):
            raise QAContractError("max_duration outcome must reach the requested duration")


def _limit_reached(elapsed: float, limit: float) -> bool:
    # Unity serializes simulation times and requested durations as C# float32.
    # Allow its relative rounding error at the boundary, without an absolute
    # tolerance that could swallow a very small positive duration entirely.
    return elapsed >= limit or isclose(elapsed, limit, rel_tol=1e-7, abs_tol=0.0)


def _validate_identifier(value: object, name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _validate_envelope(data: object) -> None:
    if not isinstance(data, Mapping):
        raise TypeError("evaluation data must be a mapping")
    version = data["schema_version"]
    validate_integer(version, name="schema_version", minimum=1)
    if version != EVALUATION_SCHEMA_VERSION:
        raise ValueError(f"unsupported evaluation schema version: {version}")
