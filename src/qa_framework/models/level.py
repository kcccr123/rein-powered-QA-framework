"""Immutable PCG recipe references for future level handoffs and evaluation replay."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from qa_framework.schema.json_data import copy_json_object, freeze_json_object
from qa_framework.schema.validation import validate_integer

LEVEL_SCHEMA_VERSION = 1
_MAX_GENERATION_SEED = 2**31 - 1


@dataclass(frozen=True, slots=True)
class LevelSpec:
    """A level identity and generation recipe, not generated or loaded geometry.

    Parameter names are preserved for the producer. Unity's current PCG uses
    fields such as ``RoomCount``, ``AreaWidth`` and ``Seed``. Empty parameters are
    allowed for reference-only/replay context. A recipe does not prove a dungeon
    has been loaded, or capture every game setting needed for deterministic
    replay. ``generator_version`` can identify the game and GenerationSettings
    revisions on which the producer relied.
    """

    level_id: str
    generation_seed: int
    generation_parameters: Mapping[str, object]
    generator_version: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.level_id, str):
            raise TypeError("level_id must be a string")
        if not self.level_id.strip():
            raise ValueError("level_id must not be empty")
        self._validate_seed(self.generation_seed, name="generation_seed")
        if self.generator_version is not None:
            if not isinstance(self.generator_version, str):
                raise TypeError("generator_version must be a string or None")
            if not self.generator_version.strip():
                raise ValueError("generator_version must not be empty")
        parameters = freeze_json_object(
            self.generation_parameters, name="generation_parameters"
        )
        if "Seed" in parameters:
            self._validate_seed(parameters["Seed"], name="generation_parameters.Seed")
            if parameters["Seed"] != self.generation_seed:
                raise ValueError("generation_parameters.Seed must equal generation_seed")
        object.__setattr__(self, "generation_parameters", parameters)

    @staticmethod
    def _validate_seed(value: object, *, name: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an integer")
        if not 0 <= value <= _MAX_GENERATION_SEED:
            raise ValueError(f"{name} must be between 0 and {_MAX_GENERATION_SEED}")

    def pcg_parameters(self) -> dict[str, object]:
        """Return a fresh recipe dictionary with Unity's exact ``Seed`` field.

        This helper prepares data only; it neither generates a level nor calls
        Unity or the PCG policy. Other parameter names and values remain intact.
        """
        parameters = copy_json_object(self.generation_parameters)
        parameters["Seed"] = self.generation_seed
        return parameters

    def to_dict(self) -> dict[str, object]:
        """Return the finite JSON handoff envelope for level schema version 1."""
        return {
            "schema_version": LEVEL_SCHEMA_VERSION,
            "level_id": self.level_id,
            "generation_seed": self.generation_seed,
            "generation_parameters": copy_json_object(self.generation_parameters),
            "generator_version": self.generator_version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> LevelSpec:
        """Validate a handoff envelope without coercing types or changing keys."""
        # JSON validation precedes decoding; the constructor validates field types.
        payload: dict[str, Any] = copy_json_object(data, name="level data")
        version = payload["schema_version"]
        validate_integer(version, name="schema_version", minimum=1)
        if version != LEVEL_SCHEMA_VERSION:
            raise ValueError(f"unsupported level schema version: {version}")
        return cls(
            level_id=payload["level_id"],
            generation_seed=payload["generation_seed"],
            generation_parameters=payload["generation_parameters"],
            generator_version=payload.get("generator_version"),
        )
