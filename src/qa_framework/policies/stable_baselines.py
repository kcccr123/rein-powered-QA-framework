"""Optional inference for gameplay checkpoints from the shared SB3 trainer."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any

from qa_framework.models.action import QAAction
from qa_framework.models.observation import QAObservation
from qa_framework.schema.action_encoding import QAActionCodec
from qa_framework.schema.constants import DEFAULT_RAY_COUNT
from qa_framework.schema.validation import (
    flatten_observation,
    get_flat_observation_size,
    validate_boolean,
    validate_integer,
    validate_observation,
)


class StableBaselinesPolicy:
    """Adapt a feedforward SB3 model to ``QAObservation -> QAAction``.

    Supply a gameplay QA checkpoint trained on this repository's schema-v1
    ``flatten_observation`` and ``QAActionCodec`` contracts, such as a model
    trained on ``GymnasiumQAEnvironment``. Matching dimensions alone cannot
    establish semantic compatibility. PCG generator checkpoints consume and
    produce different data and cannot drive gameplay. Models requiring external
    observation normalization or recurrent state are not supported here.

    Uses the existing raw-count observation encoding and normalized five-action
    codec. NumPy, Gymnasium and Stable-Baselines3 remain optional and are only
    imported when constructing/loading an inference policy, never by importing
    ``qa_framework.policies``. Checkpoint loading starts no environment or trainer.
    """

    def __init__(
        self,
        model: Any,
        *,
        ray_count: int = DEFAULT_RAY_COUNT,
        max_ability_slot: int = 0,
        deterministic: bool = True,
    ) -> None:
        validate_integer(ray_count, name="ray_count", minimum=1)
        validate_boolean(deterministic, name="deterministic")
        self.ray_count = ray_count
        self.action_codec = QAActionCodec(max_ability_slot=max_ability_slot)
        self.deterministic = deterministic
        if not callable(getattr(model, "predict", None)):
            raise TypeError("model must provide a callable predict method")
        try:
            self._np = import_module("numpy")
            spaces = import_module("gymnasium.spaces")
        except ImportError as error:
            raise ImportError(
                'Checkpoint inference requires the optional dependencies: '
                'install qa-framework[inference].'
            ) from error

        observation_space = getattr(model, "observation_space", None)
        expected_size = get_flat_observation_size(ray_count)
        if (
            not isinstance(observation_space, spaces.Box)
            or observation_space.shape != (expected_size,)
            or not self._np.issubdtype(observation_space.dtype, self._np.floating)
        ):
            raise ValueError(
                f"QA gameplay model observation space must be a floating Box of shape "
                f"({expected_size},) for {ray_count} rays; PCG checkpoints are incompatible"
            )
        action_space = getattr(model, "action_space", None)
        if (
            not isinstance(action_space, spaces.Box)
            or action_space.shape != (self.action_codec.action_size,)
            or not self._np.issubdtype(action_space.dtype, self._np.floating)
            or not self._np.all(action_space.low == -1.0)
            or not self._np.all(action_space.high == 1.0)
        ):
            raise ValueError("QA gameplay model action space must be a floating Box(-1, 1, (5,))")
        self.model = model

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        algorithm: str = "ppo",
        device: str = "cpu",
        ray_count: int = DEFAULT_RAY_COUNT,
        max_ability_slot: int = 0,
        deterministic: bool = True,
    ) -> StableBaselinesPolicy:
        """Load a PPO/A2C ``.zip`` produced by the shared trainer, for QA gameplay.

        The caller is responsible for using the same sensor geometry, ray count,
        encoding and ability-slot catalog as training. Use checkpoints from a
        trusted source: SB3 model archives contain serialized Python objects.
        """
        if not isinstance(algorithm, str):
            raise TypeError("algorithm must be a string")
        algorithm = algorithm.lower()
        if algorithm not in ("ppo", "a2c"):
            raise ValueError("algorithm must be 'ppo' or 'a2c'")
        checkpoint = Path(path)
        if not checkpoint.is_file():
            raise FileNotFoundError(f"QA policy checkpoint does not exist: {checkpoint}")
        try:
            sb3 = import_module("stable_baselines3")
        except ImportError as error:
            raise ImportError(
                'Checkpoint inference requires Stable-Baselines3: '
                'install qa-framework[inference].'
            ) from error
        model = getattr(sb3, algorithm.upper()).load(str(checkpoint), device=device)
        return cls(
            model,
            ray_count=ray_count,
            max_ability_slot=max_ability_slot,
            deterministic=deterministic,
        )

    def __call__(self, observation: QAObservation) -> QAAction:
        validate_observation(observation, ray_count=self.ray_count)
        try:
            with self._np.errstate(over="ignore", invalid="ignore"):
                values = self._np.asarray(flatten_observation(observation), dtype=self._np.float32)
        except (OverflowError, ValueError) as error:
            raise ValueError("observation cannot be encoded as finite float32 values") from error
        if not self._np.all(self._np.isfinite(values)):
            raise ValueError("observation cannot be encoded as finite float32 values")
        action, _state = self.model.predict(values, deterministic=self.deterministic)
        encoded_action = self._np.asarray(action)
        if encoded_action.shape != (self.action_codec.action_size,):
            raise ValueError("model predict must return an unbatched action of shape (5,)")
        return self.action_codec.decode(encoded_action.tolist())
