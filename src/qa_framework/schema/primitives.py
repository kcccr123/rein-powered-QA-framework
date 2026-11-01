"""Validate primitive values without loading gameplay schemas."""

from __future__ import annotations

import math


def validate_finite_number(
    value: object,
    *,
    name: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> None:
    """Require an int/float (excluding bool) that is finite and in range."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{name} must be finite")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}")


def validate_integer(value: object, *, name: str, minimum: int = 0) -> None:
    """Require a non-boolean integer with a lower bound."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    validate_finite_number(value, name=name, minimum=minimum)


def validate_boolean(value: object, *, name: str) -> None:
    """Require a boolean rather than a truthy numeric/string value."""
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a bool")
