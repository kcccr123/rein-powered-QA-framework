"""Episode outcomes; these models do not decide whether content is acceptable."""

from dataclasses import dataclass
from enum import Enum

from qa_framework.models.metrics import EpisodeMetrics
from qa_framework.schema.validation import validate_boolean


class TerminalReason(str, Enum):
    """Why an episode ended, independent of any learning algorithm."""

    COMPLETED = "completed"
    PLAYER_DIED = "player_died"
    MAX_STEPS = "max_steps"
    MAX_DURATION = "max_duration"
    ENVIRONMENT_FAILURE = "environment_failure"


@dataclass(frozen=True, slots=True)
class EpisodeResult:
    """An ended episode: either an inherent outcome or an external interruption.

    Completion and player death terminate; time/step limits and environment
    failure truncate. The flags describe lifecycle, not content acceptability.
    """

    terminated: bool
    truncated: bool
    terminal_reason: TerminalReason
    metrics: EpisodeMetrics

    def __post_init__(self) -> None:
        validate_boolean(self.terminated, name="terminated")
        validate_boolean(self.truncated, name="truncated")
        if self.terminated == self.truncated:
            raise ValueError("an episode result must be either terminated or truncated")
        if not isinstance(self.terminal_reason, TerminalReason):
            raise TypeError("terminal_reason must be a TerminalReason")
        if not isinstance(self.metrics, EpisodeMetrics):
            raise TypeError("metrics must be EpisodeMetrics")
        is_interruption = self.terminal_reason in (
            TerminalReason.MAX_STEPS,
            TerminalReason.MAX_DURATION,
            TerminalReason.ENVIRONMENT_FAILURE,
        )
        if self.truncated != is_interruption:
            raise ValueError("terminal_reason is inconsistent with termination flags")
