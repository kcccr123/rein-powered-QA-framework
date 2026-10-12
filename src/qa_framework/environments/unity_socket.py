"""Socket transport for a real Unity gameplay QA backend.

Protocol: newline-delimited JSON over TCP. Unity hosts the listener (see
``QaTcpHost`` in the kawaii-power-little-adventure repo); this module is the
client. On connect, Unity sends one handshake line shaped like
``UnityQACapabilities``; everything after that is one JSON request line
answered by one JSON reply line. The reply shapes mirror the existing
``to_dict``/``from_dict`` payloads for ``QAObservation``, ``EpisodeMetrics``
and ``EpisodeResult`` exactly, so this module decodes them with those methods
rather than inventing a parallel decoder. See
docs/integration-readiness.md for the full field-by-field contract.
"""

from __future__ import annotations

import json
import socket
from typing import Any

from qa_framework.environments.base import QAEnvironment, QAEnvironmentError
from qa_framework.environments.unity import UnityQACapabilities, UnityQARequirements
from qa_framework.evaluation.result import EpisodeResult
from qa_framework.models.action import QAAction
from qa_framework.models.episode import EpisodeConfig
from qa_framework.models.metrics import EpisodeMetrics
from qa_framework.models.observation import QAObservation
from qa_framework.schema.constants import OBSERVATION_SCHEMA_VERSION


class UnityQAEnvironment(QAEnvironment):
    """Drives a running Unity QA host over a TCP socket.

    The constructor connects immediately, reads the handshake, and rejects a
    host whose capabilities do not satisfy ``requirements`` before returning.
    A rejected or unreachable host raises before any gameplay is attempted.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8800,
        timeout_seconds: float = 30.0,
        requirements: UnityQARequirements | None = None,
    ) -> None:
        self._requirements = requirements if requirements is not None else UnityQARequirements()
        self._episode_active = False
        self._metrics = EpisodeMetrics()
        self._closed = False
        self._socket: socket.socket | None = None
        self._stream: Any = None

        self._socket = socket.create_connection((host, port), timeout=timeout_seconds)
        self._socket.settimeout(timeout_seconds)
        self._stream = self._socket.makefile("rwb")
        try:
            handshake = self._read_json()
            capabilities = UnityQACapabilities(**handshake)
            self._requirements.require_ready(capabilities)
        except Exception:
            self.close()
            raise

    @property
    def observation_schema_version(self) -> int:
        return OBSERVATION_SCHEMA_VERSION

    @property
    def episode_active(self) -> bool:
        return self._episode_active

    @property
    def last_metrics(self) -> EpisodeMetrics:
        return self._metrics

    def reset(
        self, *, seed: int | None = None, config: EpisodeConfig | None = None
    ) -> tuple[QAObservation, dict[str, object]]:
        self._ensure_open()
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
            raise TypeError("seed must be an integer or None")
        if config is not None and not isinstance(config, EpisodeConfig):
            raise TypeError("config must be EpisodeConfig or None")
        resolved_config = config if config is not None else EpisodeConfig()

        reply = self._send(self._build_reset_request(seed, resolved_config))
        self._metrics = EpisodeMetrics()
        self._episode_active = True
        observation = QAObservation.from_dict(reply["observation"])
        return observation, self._decode_info(reply["info"])

    def step(
        self, action: QAAction
    ) -> tuple[QAObservation, float, bool, bool, dict[str, object]]:
        self._ensure_open()
        if not isinstance(action, QAAction):
            raise TypeError("action must be a QAAction")
        if not self._episode_active:
            raise RuntimeError("reset must be called before stepping an active episode")

        reply = self._send({"cmd": "step", "action": action.to_dict()})

        observation = QAObservation.from_dict(reply["observation"])
        info = self._decode_info(reply["info"])
        terminated = bool(reply["terminated"])
        truncated = bool(reply["truncated"])
        episode_result = info.get("episode_result")
        if isinstance(episode_result, EpisodeResult):
            self._metrics = episode_result.metrics
        self._episode_active = not (terminated or truncated)
        return observation, float(reply["reward"]), terminated, truncated, info

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._episode_active = False
        try:
            if self._stream is not None:
                self._write_json({"cmd": "close"})
        except (OSError, QAEnvironmentError):
            pass
        finally:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
            if self._socket is not None:
                self._socket.close()
                self._socket = None

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("environment is closed")

    def _build_reset_request(self, seed: int | None, config: EpisodeConfig) -> dict[str, object]:
        # Flattened, with explicit "_provided" flags rather than JSON null, so the
        # Unity side can parse this with JsonUtility's typed (non-nullable) fields.
        request: dict[str, object] = {
            "cmd": "reset",
            "seed_provided": seed is not None,
            "seed": seed if seed is not None else 0,
            "max_steps": config.max_steps,
            "max_duration_provided": config.max_duration_seconds is not None,
            "max_duration_seconds": config.max_duration_seconds or 0.0,
            "level_provided": config.level is not None,
        }
        level = config.level
        if level is not None:
            request["level_id"] = level.level_id
            request["generation_seed"] = level.generation_seed
            request["generator_version_provided"] = level.generator_version is not None
            request["generator_version"] = level.generator_version or ""
            request["generation_parameters"] = level.pcg_parameters()
        else:
            request["level_id"] = ""
            request["generation_seed"] = 0
            request["generator_version_provided"] = False
            request["generator_version"] = ""
            request["generation_parameters"] = {}
        return request

    def _decode_info(self, raw_info: dict[str, object]) -> dict[str, object]:
        info = dict(raw_info)
        if not info.pop("level_acknowledged", False):
            info.pop("level_id", None)
            info.pop("generation_seed", None)
        if info.pop("has_episode_result", False):
            info["episode_result"] = EpisodeResult.from_dict(info["episode_result"])
        else:
            info.pop("episode_result", None)
        return info

    def _send(self, request: dict[str, object]) -> dict[str, object]:
        self._write_json(request)
        reply = self._read_json()
        if not reply.get("ok", False):
            self._episode_active = False
            raise QAEnvironmentError(str(reply.get("error", "Unity QA host reported a failure.")))
        return reply

    def _write_json(self, payload: dict[str, object]) -> None:
        try:
            line = json.dumps(payload) + "\n"
            self._stream.write(line.encode("utf-8"))
            self._stream.flush()
        except OSError as error:
            self._episode_active = False
            raise QAEnvironmentError(f"Lost connection to Unity QA host: {error}") from error

    def _read_json(self) -> dict[str, object]:
        try:
            line = self._stream.readline()
        except OSError as error:
            self._episode_active = False
            raise QAEnvironmentError(f"Lost connection to Unity QA host: {error}") from error
        if not line:
            self._episode_active = False
            raise QAEnvironmentError("Unity QA host closed the connection.")
        return json.loads(line.decode("utf-8"))
