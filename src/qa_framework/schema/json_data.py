"""Copy and freeze finite JSON data without a serializer or external dependency."""

from collections.abc import Mapping
import math
from types import MappingProxyType


def copy_json_object(data: object, *, name: str = "data") -> dict[str, object]:
    """Return independent dictionaries/lists containing only finite JSON values.

    Tuples are accepted as Python representations of JSON arrays and copied to
    lists. Mapping keys must be strings. Cycles and arbitrary Python objects are
    rejected instead of relying on a transport to silently convert them.
    """
    if not isinstance(data, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return _copy_object(data, name=name, active_ids=set())


def freeze_json_object(data: object, *, name: str = "data") -> Mapping[str, object]:
    """Deep-copy JSON data, then freeze objects as mappings and arrays as tuples."""
    copied = copy_json_object(data, name=name)
    return MappingProxyType({key: _freeze_value(value) for key, value in copied.items()})


def _copy_object(
    data: Mapping[object, object], *, name: str, active_ids: set[int]
) -> dict[str, object]:
    if id(data) in active_ids:
        raise ValueError(f"{name} must not contain cycles")
    active_ids.add(id(data))
    try:
        copied = {}
        for key, value in data.items():
            if not isinstance(key, str):
                raise TypeError(f"{name} keys must be strings")
            copied[key] = _copy_value(value, name=f"{name}.{key}", active_ids=active_ids)
        return copied
    finally:
        active_ids.remove(id(data))


def _copy_value(value: object, *, name: str, active_ids: set[int]) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
        return value
    if isinstance(value, Mapping):
        return _copy_object(value, name=name, active_ids=active_ids)
    if isinstance(value, (list, tuple)):
        if id(value) in active_ids:
            raise ValueError(f"{name} must not contain cycles")
        active_ids.add(id(value))
        try:
            return [
                _copy_value(item, name=f"{name}[{index}]", active_ids=active_ids)
                for index, item in enumerate(value)
            ]
        finally:
            active_ids.remove(id(value))
    raise TypeError(f"{name} must contain only JSON values")


def _freeze_value(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    return value
