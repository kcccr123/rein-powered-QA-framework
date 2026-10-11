"""Schema version, fixed dimensions, validation, and ML-friendly flattening."""

from qa_framework.schema.constants import DEFAULT_RAY_COUNT, OBSERVATION_SCHEMA_VERSION
from qa_framework.schema.action_encoding import ACTION_ENCODING_VERSION, QAActionCodec
from qa_framework.schema.validation import (
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
    "QAActionCodec",
    "flatten_observation",
    "get_flat_observation_size",
    "validate_action",
    "validate_observation",
    "validate_ray",
]
