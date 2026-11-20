"""Evaluate completed units against explicit developer requirements."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from qa_framework.schema.json_data import copy_json_object, freeze_json_object
from qa_framework.schema.primitives import validate_boolean, validate_integer

EVALUATION_SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class UnitReport:
    """One unit attempt with developer-defined, finite JSON evidence."""

    unit_id: str
    attempt_id: str
    evidence: Mapping[str, object]

    def __post_init__(self) -> None:
        for name, value in (("unit_id", self.unit_id), ("attempt_id", self.attempt_id)):
            if not isinstance(value, str):
                raise TypeError(f"{name} must be a string")
            if not value.strip():
                raise ValueError(f"{name} must not be empty")
        object.__setattr__(self, "evidence", freeze_json_object(self.evidence, name="evidence"))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": EVALUATION_SCHEMA_VERSION,
            "unit_id": self.unit_id,
            "attempt_id": self.attempt_id,
            "evidence": copy_json_object(self.evidence, name="evidence"),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> UnitReport:
        payload = copy_json_object(data, name="unit report")
        version = payload["schema_version"]
        validate_integer(version, name="schema_version", minimum=1)
        if version != EVALUATION_SCHEMA_VERSION:
            raise ValueError(f"unsupported unit report schema version: {version}")
        return cls(
            unit_id=payload["unit_id"],
            attempt_id=payload["attempt_id"],
            evidence=payload["evidence"],
        )


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Named requirement results and the unit evidence used to produce them."""

    report: UnitReport
    requirements: Mapping[str, bool]

    def __post_init__(self) -> None:
        if not isinstance(self.report, UnitReport):
            raise TypeError("report must be a UnitReport")
        if not isinstance(self.requirements, Mapping):
            raise TypeError("requirements must be a mapping")
        if not self.requirements:
            raise ValueError("provide at least one requirement")
        for name, passed in self.requirements.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("requirement names must be nonempty strings")
            validate_boolean(passed, name=name)
        object.__setattr__(self, "requirements", MappingProxyType(dict(self.requirements)))

    @property
    def passed(self) -> bool:
        return all(self.requirements.values())

    def to_dict(self) -> dict[str, object]:
        return {
            "report": self.report.to_dict(),
            "passed": self.passed,
            "requirements": dict(self.requirements),
        }


def evaluate_unit(
    report: UnitReport,
    requirements: Mapping[str, Callable[[UnitReport], bool]],
) -> EvaluationReport:
    """Apply named boolean predicates without imposing game-specific success rules."""
    if not isinstance(report, UnitReport):
        raise TypeError("report must be a UnitReport")
    if not isinstance(requirements, Mapping):
        raise TypeError("requirements must be a mapping")
    if not requirements:
        raise ValueError("provide at least one requirement")
    for name, check in requirements.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("requirement names must be nonempty strings")
        if not callable(check):
            raise TypeError(f"requirement {name!r} must be callable")
    results = {name: check(report) for name, check in requirements.items()}
    return EvaluationReport(report, results)
