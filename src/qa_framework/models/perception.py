"""Fixed-size perception readings, independent of the eventual raycast transport."""

from dataclasses import dataclass
from enum import Enum

from qa_framework.schema.validation import validate_ray


class QAObjectType(str, Enum):
    """Semantic categories, not Unity tags, object references, or game-specific IDs."""

    NONE = "NONE"
    OBSTACLE = "OBSTACLE"
    ENEMY = "ENEMY"
    LOOT = "LOOT"
    EXIT = "EXIT"


@dataclass(frozen=True, slots=True)
class RayObservation:
    """NONE means no classified object; a hit on an unclassified object may also use NONE.

    No hit requires NONE. A full-distance miss conventionally uses distance_01=1.0;
    partial/unclassified readings may use any distance in [0, 1].
    """

    hit: bool
    distance_01: float
    object_type: QAObjectType

    def __post_init__(self) -> None:
        validate_ray(self)
