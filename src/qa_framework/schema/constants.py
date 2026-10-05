"""Stable dimensions and encoding order for observation schema version 1."""

OBSERVATION_SCHEMA_VERSION = 1
DEFAULT_RAY_COUNT = 16

# Explicit order: enum declaration order must never silently change the encoding.
OBJECT_TYPE_ENCODING_ORDER = ("NONE", "OBSTACLE", "ENEMY", "LOOT", "EXIT")
FLAT_ACTION_SIZE = 5
FLAT_PLAYER_STATE_SIZE = 2
FLAT_RAY_SIZE = 2 + len(OBJECT_TYPE_ENCODING_ORDER)
