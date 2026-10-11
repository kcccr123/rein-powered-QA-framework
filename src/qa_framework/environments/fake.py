"""A deterministic, engine-free implementation for API development and tests."""

from math import isclose
from random import Random

from qa_framework.environments.base import QAEnvironment
from qa_framework.evaluation.result import EpisodeResult, TerminalReason
from qa_framework.models.action import QAAction
from qa_framework.models.episode import EpisodeConfig
from qa_framework.models.metrics import EpisodeMetrics
from qa_framework.models.observation import QAObservation
from qa_framework.models.perception import QAObjectType, RayObservation
from qa_framework.schema.constants import DEFAULT_RAY_COUNT, OBSERVATION_SCHEMA_VERSION
from qa_framework.schema.validation import (
    validate_action,
    validate_finite_number,
    validate_integer,
)


class FakeQAEnvironment(QAEnvironment):
    """Generate synthetic observations without simulating game mechanics.

    The same seed and action sequence produce the same observation/metric trace.
    Time is simulated as step_count * step_duration_seconds. Completion takes
    precedence over limits on the same step; max_steps precedes max_duration.
    Each reset starts a new episode with a per-instance sequential identifier.
    """

    def __init__(
        self,
        *,
        completion_steps: int = 20,
        ray_count: int = DEFAULT_RAY_COUNT,
        step_duration_seconds: float = 0.1,
    ) -> None:
        validate_integer(completion_steps, name="completion_steps", minimum=1)
        validate_integer(ray_count, name="ray_count", minimum=1)
        validate_finite_number(
            step_duration_seconds, name="step_duration_seconds", minimum=0.0
        )
        if step_duration_seconds == 0:
            raise ValueError("step_duration_seconds must be greater than zero")
        self._completion_steps = completion_steps
        self._ray_count = ray_count
        self._step_duration_seconds = step_duration_seconds
        self._random = Random()
        self._config = EpisodeConfig()
        self._metrics = EpisodeMetrics()
        self._previous_action = QAAction.noop()
        self._episode_number = 0
        self._episode_active = False
        self._closed = False

    @property
    def observation_schema_version(self) -> int:
        return OBSERVATION_SCHEMA_VERSION

    @property
    def episode_active(self) -> bool:
        return self._episode_active

    @property
    def last_metrics(self) -> EpisodeMetrics:
        return self._metrics

    def reset(
        self, *, seed: int | None = None, config: EpisodeConfig | None = None
    ) -> tuple[QAObservation, dict[str, object]]:
        """Clear metrics and previous action, and start a synthetic episode."""
        self._ensure_open()
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
            raise TypeError("seed must be an integer or None")
        if config is not None and not isinstance(config, EpisodeConfig):
            raise TypeError("config must be EpisodeConfig or None")
        self._config = config if config is not None else EpisodeConfig()
        if seed is not None:
            self._random.seed(seed)
        self._metrics = EpisodeMetrics()
        self._previous_action = QAAction.noop()
        self._episode_number += 1
        self._episode_active = True
        return self._make_observation(), self._make_info()

    def step(
        self, action: QAAction
    ) -> tuple[QAObservation, float, bool, bool, dict[str, object]]:
        """Record a validated action and advance one simulated time step."""
        self._ensure_open()
        if not self._episode_active:
            raise RuntimeError("reset must be called before stepping an active episode")
        validate_action(action)
        step_count = self._metrics.step_count + 1
        elapsed_seconds = step_count * self._step_duration_seconds
        completed = step_count >= self._completion_steps
        terminal_reason: TerminalReason | None = None
        if completed:
            terminal_reason = TerminalReason.COMPLETED
        elif step_count >= self._config.max_steps:
            terminal_reason = TerminalReason.MAX_STEPS
        elif self._config.max_duration_seconds is not None:
            duration_limit = self._config.max_duration_seconds
            # Relative tolerance handles multiplication drift at a time boundary.
            if elapsed_seconds >= duration_limit or isclose(
                elapsed_seconds, duration_limit, rel_tol=1e-12, abs_tol=0.0
            ):
                terminal_reason = TerminalReason.MAX_DURATION
        metrics = EpisodeMetrics(
            step_count=step_count,
            elapsed_seconds=elapsed_seconds,
            completed=completed,
        )
        terminated = completed
        truncated = terminal_reason is not None and not completed
        self._metrics = metrics
        self._previous_action = action
        self._episode_active = terminal_reason is None
        info = self._make_info()
        if terminal_reason is not None:
            info["episode_result"] = EpisodeResult(
                terminated=terminated,
                truncated=truncated,
                terminal_reason=terminal_reason,
                metrics=metrics,
            )
        return self._make_observation(), float(completed), terminated, truncated, info

    def close(self) -> None:
        """Mark the environment closed; retain the final metrics for inspection."""
        self._episode_active = False
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("environment is closed")

    def _make_info(self) -> dict[str, object]:
        info: dict[str, object] = {
            "schema_version": self.observation_schema_version,
            "step": self._metrics.step_count,
            "episode_id": f"fake-{self._episode_number}",
        }
        if self._config.level is not None:
            # Echo provenance for contract tests; no content is loaded or generated.
            info["level_id"] = self._config.level.level_id
            info["generation_seed"] = self._config.level.generation_seed
        return info

    def _make_observation(self) -> QAObservation:
        object_types = (
            QAObjectType.OBSTACLE,
            QAObjectType.ENEMY,
            QAObjectType.LOOT,
            QAObjectType.EXIT,
        )
        rays: list[RayObservation] = []
        for _ in range(self._ray_count):
            hit = self._random.choice((False, True))
            rays.append(
                RayObservation(
                    hit=hit,
                    distance_01=self._random.random() if hit else 1.0,
                    object_type=self._random.choice(object_types) if hit else QAObjectType.NONE,
                )
            )
        return QAObservation(
            health_01=1.0,
            powerup_count=0,
            rays=tuple(rays),
            previous_action=self._previous_action,
            ray_count=self._ray_count,
        )
