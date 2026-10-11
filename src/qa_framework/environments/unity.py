"""Offline requirements for a future UnityQAEnvironment; no transport is implemented.

The sibling shared RL package uses RlProtocol over TCP, rather than ML-Agents.
Its legacy CONFIG/OBS/REW/DONE messages do not carry the level, schema, metrics,
or terminal evidence required here. These Python records describe proposed
capability checks, not an extension already supported by the Unity wire protocol.
"""

from __future__ import annotations

from dataclasses import dataclass

from qa_framework.schema.action_encoding import ACTION_ENCODING_VERSION
from qa_framework.schema.constants import DEFAULT_RAY_COUNT, OBSERVATION_SCHEMA_VERSION
from qa_framework.schema.validation import (
    get_flat_observation_size,
    validate_boolean,
    validate_integer,
)


class UnityQAReadinessError(RuntimeError):
    """The supplied backend cannot fulfill the gameplay QA contract."""


@dataclass(frozen=True, slots=True)
class UnityQACapabilities:
    """Evidence a future backend must obtain explicitly, rather than infer from DONE.

    Observation/action sizes alone never prove QA compatibility. Unavailable
    capabilities default to unknown/false so legacy bridge descriptions fail closed.
    """

    observation_size: int
    action_size: int
    role: str | None = None
    observation_schema_version: int | None = None
    ray_count: int | None = None
    action_encoding_version: int | None = None
    max_ability_slot: int | None = None
    supports_level_recipe: bool = False
    acknowledges_level_identity: bool = False
    reports_terminal_reason: bool = False
    reports_episode_metrics: bool = False
    separates_termination_flags: bool = False

    def __post_init__(self) -> None:
        for name in ("observation_size", "action_size"):
            validate_integer(getattr(self, name), name=name)
        for name in (
            "observation_schema_version", "ray_count", "action_encoding_version",
            "max_ability_slot",
        ):
            value = getattr(self, name)
            if value is not None:
                validate_integer(value, name=name)
        if self.role is not None and not isinstance(self.role, str):
            raise TypeError("role must be a string or None")
        for name in (
            "supports_level_recipe", "acknowledges_level_identity",
            "reports_terminal_reason", "reports_episode_metrics",
            "separates_termination_flags",
        ):
            validate_boolean(getattr(self, name), name=name)


@dataclass(frozen=True, slots=True)
class UnityQARequirements:
    """Requirements for the existing QA schema and optional normalized RL codec."""

    ray_count: int = DEFAULT_RAY_COUNT
    max_ability_slot: int = 0

    def __post_init__(self) -> None:
        validate_integer(self.ray_count, name="ray_count", minimum=1)
        validate_integer(self.max_ability_slot, name="max_ability_slot")

    @property
    def observation_size(self) -> int:
        return get_flat_observation_size(self.ray_count)

    @property
    def action_size(self) -> int:
        return 5

    def readiness_issues(self, capabilities: UnityQACapabilities) -> tuple[str, ...]:
        """Compare explicit capability evidence without touching Unity or the network."""
        if not isinstance(capabilities, UnityQACapabilities):
            raise TypeError("capabilities must be UnityQACapabilities")
        issues = []
        expected = {
            "role": "qa_gameplay",
            "observation_size": self.observation_size,
            "action_size": self.action_size,
            "observation_schema_version": OBSERVATION_SCHEMA_VERSION,
            "ray_count": self.ray_count,
            "action_encoding_version": ACTION_ENCODING_VERSION,
            "max_ability_slot": self.max_ability_slot,
        }
        for name, value in expected.items():
            if getattr(capabilities, name) != value:
                issues.append(f"{name} must be {value!r}; got {getattr(capabilities, name)!r}")
        required = {
            "supports_level_recipe": "reset must accept the selected level recipe",
            "acknowledges_level_identity": "reset/step must acknowledge level identity and seed",
            "reports_terminal_reason": "ending steps must report explicit completion/failure reasons",
            "reports_episode_metrics": "episode metrics must accompany outcomes",
            "separates_termination_flags": "termination and truncation must be distinct",
        }
        issues.extend(message for name, message in required.items() if not getattr(capabilities, name))
        return tuple(issues)

    def require_ready(self, capabilities: UnityQACapabilities) -> None:
        """Reject legacy or incomplete capability evidence before a future rollout."""
        issues = self.readiness_issues(capabilities)
        if issues:
            raise UnityQAReadinessError("Unity gameplay QA is not ready: " + "; ".join(issues))
