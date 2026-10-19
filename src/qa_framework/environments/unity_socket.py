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
from dataclasses import replace
from math import isclose
from typing import Any

from qa_framework.environments.base import QAEnvironment, QAEnvironmentError
from qa_framework.environments.unity import UnityQACapabilities, UnityQARequirements
from qa_framework.evaluation.result import EpisodeResult
from qa_framework.evaluation.runner import QAContractError
from qa_framework.models.action import QAAction
from qa_framework.models.episode import EpisodeConfig
from qa_framework.models.metrics import EpisodeMetrics
from qa_framework.models.observation import QAObservation
from qa_framework.schema.constants import OBSERVATION_SCHEMA_VERSION
from qa_framework.schema.validation import (
    validate_action, validate_boolean, validate_finite_number, validate_integer,
    validate_observation,
)


class UnityQAEnvironment(QAEnvironment):
    """Drives a running Unity QA host over a TCP socket.

    The constructor connects immediately, reads the handshake, and rejects a
    host whose capabilities do not satisfy ``requirements`` before returning.
    A rejected or unreachable host raises before any gameplay is attempted.

    The current host reports full metrics only on ending steps. Intermediate
    snapshots contain the acknowledged step count and simulated time; event
    fields remain unobserved zero defaults. ``step_duration_seconds`` must match
    QaPlayerController (currently 0.1); it is not negotiated by the handshake.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8800,
        timeout_seconds: float = 30.0,
        requirements: UnityQARequirements | None = None,
        step_duration_seconds: float = 0.1,
    ) -> None:
        validate_finite_number(timeout_seconds, name="timeout_seconds", minimum=0.0)
        validate_finite_number(step_duration_seconds, name="step_duration_seconds", minimum=0.0)
        if timeout_seconds == 0 or step_duration_seconds == 0:
            raise ValueError("timeout_seconds and step_duration_seconds must be positive")
        self._requirements = requirements if requirements is not None else UnityQARequirements()
        self._step_duration_seconds = step_duration_seconds
        self._episode_active = False
        self._metrics = EpisodeMetrics()
        self._closed = False
        self._socket: socket.socket | None = None
        self._stream: Any = None

        try:
            self._socket = socket.create_connection((host, port), timeout=timeout_seconds)
            self._socket.settimeout(timeout_seconds)
            self._stream = self._socket.makefile("rwb")
            handshake = self._read_json()
            capabilities = UnityQACapabilities(**handshake)
            self._requirements.require_ready(capabilities)
        except OSError as error:
            self.close()
            raise QAEnvironmentError(f"Cannot connect to Unity QA host: {error}") from error
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

        try:
            reply = self._send(self._build_reset_request(seed, resolved_config))
            observation = self._decode_observation(reply["observation"], QAAction.noop())
            info = self._decode_info(reply["info"])
            if info.get("step") != 0 or "episode_result" in info:
                raise QAContractError("reset must acknowledge step zero without an episode result")
        except Exception:
            self.close()
            raise
        self._metrics = EpisodeMetrics()
        self._episode_active = True
        return observation, info

    def step(
        self, action: QAAction
    ) -> tuple[QAObservation, float, bool, bool, dict[str, object]]:
        self._ensure_open()
        if not isinstance(action, QAAction):
            raise TypeError("action must be a QAAction")
        validate_action(action, max_ability_slot=self._requirements.max_ability_slot)
        if not self._episode_active:
            raise RuntimeError("reset must be called before stepping an active episode")

        try:
            reply = self._send({"cmd": "step", "action": action.to_dict()})
            observation = self._decode_observation(reply["observation"], action)
            info = self._decode_info(reply["info"])
            terminated, truncated = reply["terminated"], reply["truncated"]
            validate_boolean(terminated, name="terminated")
            validate_boolean(truncated, name="truncated")
            reward = reply["reward"]
            validate_finite_number(reward, name="reward")
            step = info["step"]
            if step != self._metrics.step_count + 1:
                raise QAContractError("Unity must acknowledge each applied step once")
            episode_result = info.get("episode_result")
            if terminated or truncated:
                if not isinstance(episode_result, EpisodeResult):
                    raise QAContractError("ending steps require an episode result")
                if (episode_result.terminated, episode_result.truncated) != (terminated, truncated):
                    raise QAContractError("episode result flags must match step flags")
                if episode_result.metrics.step_count != step:
                    raise QAContractError("episode result metrics must match the acknowledged step")
                # JsonUtility rounds C# float32 simulation times. Restore the exact
                # configured step clock only when the reported value agrees with it.
                metrics = episode_result.metrics
                elapsed = step * self._step_duration_seconds
                if isclose(metrics.elapsed_seconds, elapsed, rel_tol=1e-6, abs_tol=1e-7):
                    info["reported_elapsed_seconds"] = metrics.elapsed_seconds
                    metrics = replace(metrics, elapsed_seconds=elapsed)
                    info["episode_result"] = replace(episode_result, metrics=metrics)
                info["metrics_source"] = "unity_episode_result"
            else:
                if episode_result is not None:
                    raise QAContractError("active steps cannot contain an episode result")
                # The existing host supplies combat metrics only on ending steps.
                # Keep progress available to evaluate_episode using its acknowledged
                # counter and QaPlayerController's fixed simulation step clock.
                metrics = EpisodeMetrics(
                    step_count=step, elapsed_seconds=step * self._step_duration_seconds,
                )
                info["metrics_source"] = "step_bookkeeping"
        except Exception:
            self.close()
            raise
        self._metrics = metrics
        self._episode_active = not (terminated or truncated)
        return observation, float(reward), terminated, truncated, info

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
                try:
                    self._stream.close()
                except OSError:
                    pass
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
        if not isinstance(raw_info, dict):
            raise QAContractError("Unity info must be an object")
        info = dict(raw_info)
        validate_integer(info["step"], name="step")
        acknowledged = info.pop("level_acknowledged", False)
        validate_boolean(acknowledged, name="level_acknowledged")
        if not acknowledged:
            info.pop("level_id", None)
            info.pop("generation_seed", None)
        has_result = info.pop("has_episode_result", False)
        validate_boolean(has_result, name="has_episode_result")
        if has_result:
            info["episode_result"] = EpisodeResult.from_dict(info["episode_result"])
        else:
            info.pop("episode_result", None)
        return info

    def _decode_observation(self, raw: object, applied: QAAction) -> QAObservation:
        observation = QAObservation.from_dict(raw)
        validate_observation(observation, ray_count=self._requirements.ray_count)
        previous = observation.previous_action
        if (
            not isclose(previous.move_x, applied.move_x, rel_tol=1e-6, abs_tol=1e-7)
            or not isclose(previous.move_y, applied.move_y, rel_tol=1e-6, abs_tol=1e-7)
            or (previous.attack, previous.interact, previous.ability)
            != (applied.attack, applied.interact, applied.ability)
        ):
            raise QAContractError("Unity previous_action must acknowledge the applied action")
        return replace(observation, previous_action=applied)

    def _send(self, request: dict[str, object]) -> dict[str, object]:
        self._write_json(request)
        reply = self._read_json()
        validate_boolean(reply.get("ok"), name="ok")
        if not reply["ok"]:
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
        try:
            payload = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise QAContractError("Unity reply must be valid UTF-8 JSON") from error
        if not isinstance(payload, dict):
            raise QAContractError("Unity reply must be a JSON object")
        return payload
