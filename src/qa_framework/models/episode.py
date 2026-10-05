"""Caller-supplied limits and context for one QA episode."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from qa_framework.schema.validation import validate_finite_number, validate_integer


@dataclass(frozen=True, slots=True)
class EpisodeConfig:
    """Generic episode limits; metadata is a shallow, read-only snapshot.

    Metadata values remain caller-defined objects. They are not observation data
    and should be JSON-compatible if callers intend to serialize them.
    """

    max_steps: int = 1000
    max_duration_seconds: float | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_integer(self.max_steps, name="max_steps", minimum=1)
        if self.max_duration_seconds is not None:
            validate_finite_number(
                self.max_duration_seconds, name="max_duration_seconds", minimum=0.0
            )
            if self.max_duration_seconds == 0:
                raise ValueError("max_duration_seconds must be greater than zero")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        if any(not isinstance(key, str) for key in self.metadata):
            raise TypeError("metadata keys must be strings")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
