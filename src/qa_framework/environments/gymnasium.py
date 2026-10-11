"""Optional Gymnasium adapter for an explicitly supplied QA environment.

Import this module only when the ``rl`` extra is installed. It never constructs
a Unity environment, starts an executable, connects a transport, or trains a
policy. The supplied environment controls all gameplay and episode behavior.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import gymnasium as gym
import numpy as np

from qa_framework.environments.base import QAEnvironment
from qa_framework.models.episode import EpisodeConfig
from qa_framework.models.observation import QAObservation
from qa_framework.schema.action_encoding import QAActionCodec
from qa_framework.schema.constants import DEFAULT_RAY_COUNT, OBSERVATION_SCHEMA_VERSION
from qa_framework.schema.validation import (
    flatten_observation,
    get_flat_observation_size,
    validate_integer,
    validate_observation,
)


class GymnasiumQAEnvironment(gym.Env):
    """Expose schema v1 observations and a normalized five-value action Box.

    Observation encoding remains the existing QA schema: health, raw powerup
    count, previous action, and ordered rays. Counts and previous ability slots
    remain raw numbers rather than normalized policy action values.

    Pass ``options={"config": EpisodeConfig(...)}`` to reset to select episode
    limits and level context. An omitted config uses ``default_config``. Info,
    including terminal EpisodeResult objects, is passed through unchanged.
    Malformed backend observations retire the environment before propagating
    the validation error, since its gameplay state has already advanced.
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        environment: QAEnvironment,
        *,
        ray_count: int = DEFAULT_RAY_COUNT,
        max_ability_slot: int = 0,
        default_config: EpisodeConfig | None = None,
    ) -> None:
        if not isinstance(environment, QAEnvironment):
            raise TypeError("environment must implement QAEnvironment")
        validate_integer(ray_count, name="ray_count", minimum=1)
        if default_config is not None and not isinstance(default_config, EpisodeConfig):
            raise TypeError("default_config must be EpisodeConfig or None")
        self.qa_environment = environment
        self.ray_count = ray_count
        self.action_codec = QAActionCodec(max_ability_slot=max_ability_slot)
        self.default_config = default_config
        self.render_mode = None
        self._closed = False
        self._validate_schema_version()
        self.action_space = gym.spaces.Box(
            low=-1.0, high=1.0, shape=(self.action_codec.action_size,), dtype=np.float32
        )
        size = get_flat_observation_size(ray_count)
        low = np.zeros(size, dtype=np.float32)
        high = np.ones(size, dtype=np.float32)
        low[2:4] = -1.0
        high[[1, 6]] = np.inf
        self.observation_space = gym.spaces.Box(low=low, high=high, dtype=np.float32)

    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[np.ndarray, dict[str, object]]:
        """Validate reset inputs before invoking the supplied environment."""
        self._ensure_open()
        self._validate_schema_version()
        if seed is not None:
            validate_integer(seed, name="seed")
        config = self.default_config
        if options is not None:
            if not isinstance(options, Mapping):
                raise TypeError("reset options must be a mapping or None")
            if any(key != "config" for key in options):
                raise ValueError("the only supported reset option is 'config'")
            if "config" in options:
                config = options["config"]
                if not isinstance(config, EpisodeConfig):
                    raise TypeError("reset option 'config' must be EpisodeConfig")
        super().reset(seed=seed)
        observation, info = self.qa_environment.reset(seed=seed, config=config)
        return self._encode_observation(observation, info), info

    def step(
        self, action: np.ndarray | Sequence[float]
    ) -> tuple[np.ndarray, float, bool, bool, dict[str, object]]:
        """Decode a valid action before advancing the supplied environment."""
        self._ensure_open()
        self._validate_schema_version()
        if isinstance(action, np.ndarray):
            if action.shape != (self.action_codec.action_size,):
                raise ValueError("encoded action must have shape (5,)")
            values = action.tolist()
        elif isinstance(action, Sequence) and not isinstance(action, (str, bytes, bytearray)):
            # NumPy scalars inside an ordinary sequence also become Python scalars.
            values = [value.item() if isinstance(value, np.generic) else value for value in action]
        else:
            raise TypeError("encoded action must be a numeric sequence or NumPy array")
        command = self.action_codec.decode(values)
        observation, reward, terminated, truncated, info = self.qa_environment.step(command)
        return self._encode_observation(observation, info), reward, terminated, truncated, info

    def close(self) -> None:
        """Delegate cleanup once, retaining the underlying episode results."""
        if not self._closed:
            self.qa_environment.close()
            self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("environment is closed")

    def _validate_schema_version(self) -> None:
        version = self.qa_environment.observation_schema_version
        validate_integer(version, name="observation_schema_version", minimum=1)
        if version != OBSERVATION_SCHEMA_VERSION:
            raise ValueError(f"unsupported observation schema version: {version}")

    def _encode_observation(
        self, observation: QAObservation, info: dict[str, object]
    ) -> np.ndarray:
        try:
            return self._checked_observation(observation, info)
        except Exception as error:
            try:
                self.close()
            except Exception as cleanup_error:
                raise error from cleanup_error
            raise

    def _checked_observation(
        self, observation: QAObservation, info: dict[str, object]
    ) -> np.ndarray:
        self._validate_schema_version()
        if not isinstance(info, dict):
            raise TypeError("environment info must be a dictionary")
        if "schema_version" in info:
            version = info["schema_version"]
            validate_integer(version, name="info schema_version", minimum=1)
            if version != OBSERVATION_SCHEMA_VERSION:
                raise ValueError(f"unsupported observation schema version in info: {version}")
        validate_observation(observation, ray_count=self.ray_count)
        try:
            with np.errstate(over="ignore", invalid="ignore"):
                values = np.asarray(flatten_observation(observation), dtype=np.float32)
        except (OverflowError, ValueError) as error:
            raise ValueError("observation cannot be encoded as finite float32 values") from error
        if not np.all(np.isfinite(values)):
            raise ValueError("observation cannot be encoded as finite float32 values")
        return values
