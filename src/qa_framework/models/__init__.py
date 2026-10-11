"""Typed domain models shared by environment adapters and external trainers."""

from qa_framework.models.action import QAAction, action_from_tuple, action_to_tuple
from qa_framework.models.episode import EpisodeConfig
from qa_framework.models.level import LEVEL_SCHEMA_VERSION, LevelSpec
from qa_framework.models.metrics import EpisodeMetrics
from qa_framework.models.observation import QAObservation
from qa_framework.models.perception import QAObjectType, RayObservation

__all__ = [
    "LEVEL_SCHEMA_VERSION",
    "EpisodeConfig",
    "EpisodeMetrics",
    "LevelSpec",
    "QAAction",
    "QAObjectType",
    "QAObservation",
    "RayObservation",
    "action_from_tuple",
    "action_to_tuple",
]
