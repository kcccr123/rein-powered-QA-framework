"""Exercise the current Unity JSON wire contract using a local TCP host."""

from contextlib import contextmanager
import json
import socket
from threading import Thread

import pytest

from qa_framework import (
    EpisodeConfig, EpisodeMetrics, EpisodeResult, EvaluationRequest, LevelSpec,
    QAAction, QAContractError, QAObjectType, QAObservation, RayObservation,
    TerminalReason, UnityQACapabilities, UnityQAEnvironment, evaluate_episode,
)


def observation(action=QAAction(), *, ray_count=16):
    return QAObservation(
        health_01=0.6, powerup_count=1,
        rays=tuple(RayObservation(False, 1.0, QAObjectType.NONE) for _ in range(ray_count)),
        previous_action=action, ray_count=ray_count,
    ).to_dict()


def reply(step, action=QAAction(), *, ended=False):
    info = {
        "schema_version": 1, "step": step, "episode_id": "unity-1",
        "level_acknowledged": True, "level_id": "candidate", "generation_seed": 7,
        "has_episode_result": ended, "episode_result": None,
    }
    if ended:
        info["episode_result"] = EpisodeResult(
            True, False, TerminalReason.COMPLETED,
            EpisodeMetrics(step_count=step, elapsed_seconds=step * 0.1,
                           completed=True, damage_taken=4, stuck_events=2),
        ).to_dict()
    return {
        "ok": True, "observation": observation(action), "info": info,
        "reward": 1.0 if ended else 0.0,
        "terminated": ended, "truncated": False,
    }


@contextmanager
def unity_host(responses):
    """One real socket session; responses can inspect the received request."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(3)
    errors, requests = [], []

    def serve():
        try:
            connection, _ = listener.accept()
            with connection, connection.makefile("rwb") as stream:
                capabilities = UnityQACapabilities(
                    observation_size=119, action_size=5, role="qa_gameplay",
                    observation_schema_version=1, ray_count=16,
                    action_encoding_version=1, max_ability_slot=0,
                    supports_level_recipe=True, acknowledges_level_identity=True,
                    reports_terminal_reason=True, reports_episode_metrics=True,
                    separates_termination_flags=True,
                )
                from dataclasses import asdict
                stream.write((json.dumps(asdict(capabilities)) + "\n").encode())
                stream.flush()
                for response in responses:
                    line = stream.readline()
                    if not line:
                        break
                    request = json.loads(line)
                    requests.append(request)
                    value = response(request) if callable(response) else response
                    stream.write((json.dumps(value) + "\n").encode())
                    stream.flush()
                # The client owns session cleanup, including after invalid data.
                closing = stream.readline()
                if closing:
                    requests.append(json.loads(closing))
        except Exception as error:
            errors.append(error)

    thread = Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1], requests
    finally:
        thread.join(timeout=4)
        listener.close()
        assert not thread.is_alive(), "mock Unity host did not finish"
        assert not errors, errors


def test_live_evaluation_has_progress_metrics_and_reuses_connection():
    action = QAAction(move_x=0.7071067811865476, move_y=0.7071067811865476)
    acknowledged = QAAction(move_x=0.70710677, move_y=0.70710677)
    responses = [reply(0), reply(1, acknowledged), reply(2, acknowledged, ended=True)] * 2
    with unity_host(responses) as (port, requests):
        env = UnityQAEnvironment(port=port)
        try:
            for _ in range(2):
                report = evaluate_episode(
                    env, lambda obs: action,
                    EvaluationRequest("evaluation", LevelSpec("candidate", 7, {"RoomCount": 10}),
                                      config=EpisodeConfig(max_steps=5)),
                )
                assert report.result.terminal_reason is TerminalReason.COMPLETED
                assert report.result.metrics.damage_taken == 4
                assert report.result.metrics.stuck_events == 2
                assert report.total_reward == 1
        finally:
            env.close()
        assert requests[0]["generation_parameters"] == {"RoomCount": 10, "Seed": 7}
        assert requests[0]["seed_provided"]
        assert requests[1]["action"] == action.to_dict()


def test_interim_metrics_do_not_invent_combat_measurements():
    with unity_host([reply(0), reply(1)]) as (port, _):
        env = UnityQAEnvironment(port=port)
        try:
            env.reset()
            obs, _, _, _, info = env.step(QAAction())
            assert obs.health_01 == 0.6 and obs.powerup_count == 1
            assert env.last_metrics == EpisodeMetrics(step_count=1, elapsed_seconds=0.1)
            assert info["metrics_source"] == "step_bookkeeping"
        finally:
            env.close()


@pytest.mark.parametrize("mutation", ["rays", "action", "flags", "step", "missing_result"])
def test_malformed_step_retires_session(mutation):
    payload = reply(1)
    if mutation == "rays":
        payload["observation"] = observation(ray_count=8)
    elif mutation == "action":
        payload["observation"] = observation(QAAction(move_x=0.8))
    elif mutation == "flags":
        payload["terminated"] = "false"
    elif mutation == "step":
        payload["info"]["step"] = 5
    else:
        payload["terminated"] = True
    with unity_host([reply(0), payload]) as (port, _):
        env = UnityQAEnvironment(port=port)
        env.reset()
        with pytest.raises((QAContractError, TypeError, ValueError)):
            env.step(QAAction())
        assert not env.episode_active
        with pytest.raises(RuntimeError, match="closed"):
            env.reset()


def test_host_error_becomes_environment_failure_with_confirmed_progress():
    with unity_host([reply(0), reply(1), {"ok": False, "error": "No active remote episode."}]) as (port, _):
        env = UnityQAEnvironment(port=port)
        report = evaluate_episode(
            env, lambda _: QAAction(),
            EvaluationRequest("evaluation", LevelSpec("candidate", 7, {})),
        )
        assert report.result.terminal_reason is TerminalReason.ENVIRONMENT_FAILURE
        assert report.result.metrics.step_count == 1
        assert "No active remote episode" in report.error


def test_float32_duration_boundary_remains_a_valid_truncation():
    ending = reply(3)
    ending["truncated"] = True
    ending["info"]["has_episode_result"] = True
    ending["info"]["episode_result"] = EpisodeResult(
        False, True, TerminalReason.MAX_DURATION,
        EpisodeMetrics(step_count=3, elapsed_seconds=0.3),
    ).to_dict()
    with unity_host([reply(0), reply(1), reply(2), ending]) as (port, _):
        env = UnityQAEnvironment(port=port)
        try:
            report = evaluate_episode(
                env, lambda _: QAAction(),
                EvaluationRequest("evaluation", LevelSpec("candidate", 7, {}),
                                  config=EpisodeConfig(max_duration_seconds=0.300000001)),
            )
            assert report.result.terminal_reason is TerminalReason.MAX_DURATION
            assert report.result.metrics.elapsed_seconds == pytest.approx(0.3)
        finally:
            env.close()


def test_unsupported_ability_does_not_send_a_step():
    with unity_host([reply(0)]) as (port, requests):
        env = UnityQAEnvironment(port=port)
        try:
            env.reset()
            with pytest.raises(ValueError, match="ability"):
                env.step(QAAction(ability=1))
            assert env.episode_active
        finally:
            env.close()
        assert all(item["cmd"] != "step" for item in requests)
