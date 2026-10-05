"""Portable QA environment contracts; no Unity or learning framework is required."""

from qa_framework.environments import FakeQAEnvironment, QAEnvironment
from qa_framework.evaluation import EpisodeResult, TerminalReason
from qa_framework.models import (
    EpisodeConfig,
    EpisodeMetrics,
    QAAction,
    QAObjectType,
    QAObservation,
    RayObservation,
    action_from_tuple,
    action_to_tuple,
)
from qa_framework.schema import (
    DEFAULT_RAY_COUNT,
    OBSERVATION_SCHEMA_VERSION,
    flatten_observation,
    get_flat_observation_size,
    validate_action,
    validate_observation,
    validate_ray,
)

__all__ = [
    "DEFAULT_RAY_COUNT",
    "OBSERVATION_SCHEMA_VERSION",
    "EpisodeConfig",
    "EpisodeMetrics",
    "EpisodeResult",
    "FakeQAEnvironment",
    "QAAction",
    "QAEnvironment",
    "QAObjectType",
    "QAObservation",
    "RayObservation",
    "TerminalReason",
    "action_from_tuple",
    "action_to_tuple",
    "flatten_observation",
    "get_flat_observation_size",
    "validate_action",
    "validate_observation",
    "validate_ray",
]
