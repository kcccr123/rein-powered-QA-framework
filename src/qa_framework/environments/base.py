"""The small environment API consumed by an external RL framework."""

from abc import ABC, abstractmethod

from qa_framework.models.action import QAAction
from qa_framework.models.episode import EpisodeConfig
from qa_framework.models.metrics import EpisodeMetrics
from qa_framework.models.observation import QAObservation


class QAEnvironmentError(RuntimeError):
    """Infrastructure failure from an adapter, distinct from an invalid policy action.

    Future transports translate disconnects, timeouts and level-loading failures
    into this exception. Callers retire the session rather than infer gameplay loss.
    """


class QAEnvironment(ABC):
    """Explicit adapter contract, with no engine or transport dependencies.

    Reset starts an episode and returns its initial observation. Step applies one
    valid action and returns (observation, reward, terminated, truncated, info).
    Call reset again after termination or truncation. Close releases resources
    and must be safe to repeat. Implementations define their timing semantics.
    """

    @abstractmethod
    def reset(
        self, *, seed: int | None = None, config: EpisodeConfig | None = None
    ) -> tuple[QAObservation, dict[str, object]]:
        """Start a new episode; previous_action in its observation is NOOP.

        When config.level is supplied, load that candidate and acknowledge its
        level_id and generation_seed in info on reset and every step. Adapters
        that cannot do this must reject the request before starting gameplay.
        """
        raise NotImplementedError

    @abstractmethod
    def step(
        self, action: QAAction
    ) -> tuple[QAObservation, float, bool, bool, dict[str, object]]:
        """Apply an action during an active episode and report the next state."""
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        """Release resources. Repeated calls must be safe."""
        raise NotImplementedError

    @property
    @abstractmethod
    def observation_schema_version(self) -> int:
        """Version of the observation encoder contract."""
        raise NotImplementedError

    @property
    @abstractmethod
    def episode_active(self) -> bool:
        """Whether step may currently be called."""
        raise NotImplementedError

    @property
    @abstractmethod
    def last_metrics(self) -> EpisodeMetrics:
        """Latest immutable metrics; reset clears the previous episode's values."""
        raise NotImplementedError
