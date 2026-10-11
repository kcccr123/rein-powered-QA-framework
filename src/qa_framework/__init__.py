"""Portable QA environment contracts; no Unity or learning framework is required."""

from qa_framework.environments import (
    FakeQAEnvironment, QAEnvironment, QAEnvironmentError,
    UnityQACapabilities, UnityQAReadinessError, UnityQARequirements,
)
from qa_framework.evaluation import (
    EVALUATION_SCHEMA_VERSION, EpisodeResult, EvaluationReport, EvaluationRequest,
    QAContractError, QAPolicy, TerminalReason, evaluate_episode,
)
from qa_framework.models import (
    LEVEL_SCHEMA_VERSION,
    EpisodeConfig,
    EpisodeMetrics,
    LevelSpec,
    QAAction,
    QAObjectType,
    QAObservation,
    RayObservation,
    action_from_tuple,
    action_to_tuple,
)
from qa_framework.schema import (
    ACTION_ENCODING_VERSION,
    DEFAULT_RAY_COUNT,
    OBSERVATION_SCHEMA_VERSION,
    QAActionCodec,
    flatten_observation,
    get_flat_observation_size,
    validate_action,
    validate_observation,
    validate_ray,
)

__all__ = [
    "ACTION_ENCODING_VERSION",
    "DEFAULT_RAY_COUNT",
    "OBSERVATION_SCHEMA_VERSION",
    "EVALUATION_SCHEMA_VERSION",
    "LEVEL_SCHEMA_VERSION",
    "EpisodeConfig",
    "EpisodeMetrics",
    "EpisodeResult",
    "EvaluationReport",
    "EvaluationRequest",
    "FakeQAEnvironment",
    "LevelSpec",
    "QAAction",
    "QAActionCodec",
    "QAContractError",
    "QAEnvironment",
    "QAEnvironmentError",
    "QAObjectType",
    "QAObservation",
    "QAPolicy",
    "RayObservation",
    "TerminalReason",
    "UnityQACapabilities",
    "UnityQAReadinessError",
    "UnityQARequirements",
    "action_from_tuple",
    "action_to_tuple",
    "evaluate_episode",
    "flatten_observation",
    "get_flat_observation_size",
    "validate_action",
    "validate_observation",
    "validate_ray",
]
