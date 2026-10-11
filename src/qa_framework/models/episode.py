"""Caller-supplied limits and context for one QA episode."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from qa_framework.models.level import LevelSpec
from qa_framework.schema.json_data import copy_json_object
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
    level: LevelSpec | None = None

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
        if self.level is not None and not isinstance(self.level, LevelSpec):
            raise TypeError("level must be a LevelSpec or None")

    def to_dict(self) -> dict[str, object]:
        """Copy limits and context to JSON, rejecting non-JSON metadata here.

        Construction still permits arbitrary metadata objects for local callers.
        A transport handoff requires finite JSON values and string object keys.
        """
        return {
            "max_steps": self.max_steps,
            "max_duration_seconds": (
                None if self.max_duration_seconds is None else float(self.max_duration_seconds)
            ),
            "metadata": copy_json_object(self.metadata, name="metadata"),
            "level": None if self.level is None else self.level.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> EpisodeConfig:
        """Restore serialized episode limits and optional versioned level context."""
        # JSON validation precedes decoding; the constructor validates field types.
        payload: dict[str, Any] = copy_json_object(data, name="episode config data")
        level: Any = payload.get("level")
        return cls(
            max_steps=payload["max_steps"],
            max_duration_seconds=payload["max_duration_seconds"],
            metadata=payload["metadata"],
            level=None if level is None else LevelSpec.from_dict(level),
        )
