"""Own a Unity Player process and persist its reports before confirming completion."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import time
from uuid import uuid4

from qa_framework.evaluation.runner import EVALUATION_SCHEMA_VERSION, UnitReport
from qa_framework.schema.json_data import copy_json_object

PLATFORM_PROTOCOL_VERSION = 1
STARTUP_TIMEOUT = 30.0
COMPLETION_TIMEOUT = 30.0
SHUTDOWN_TIMEOUT = 5.0
TERMINATE_TIMEOUT = 2.0


class TrialError(RuntimeError):
    """The trial did not complete with confirmed saved output."""


def load_assignment(path: Path) -> dict:
    assignment = copy_json_object(json.loads(path.read_text(encoding="utf-8")), name="assignment")
    for key in ("suite_id", "trial_id"):
        if not isinstance(assignment.get(key), str) or not assignment[key].strip():
            raise ValueError(f"{key} must be a nonempty string")
    count = assignment.get("episodes_per_trial")
    if type(count) is not int or count <= 0:
        raise ValueError("episodes_per_trial must be a positive integer")
    for key in ("environment", "agent"):
        definition = assignment.get(key)
        if not isinstance(definition, dict):
            raise ValueError(f"{key} must be an object")
        identity = definition.get("id")
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError(f"{key}.id must be a nonempty string")
    return assignment


class TrialConnection:
    def __init__(self, connection: socket.socket, player: subprocess.Popen) -> None:
        self.connection = connection
        self.player = player
        self.buffer = b""

    def send(self, message: dict) -> None:
        self.connection.sendall((json.dumps(message, allow_nan=False) + "\n").encode("utf-8"))

    def receive(self, deadline: float | None = None) -> dict:
        # Keep partial TCP lines across polls while also observing process exit.
        while b"\n" not in self.buffer:
            if self.player.poll() is not None:
                raise TrialError(f"Unity exited before confirmed completion (code {self.player.returncode})")
            if deadline is not None and time.monotonic() >= deadline:
                raise TrialError("Timed out waiting for trial completion")
            if not select.select([self.connection], [], [], 0.1)[0]:
                continue
            chunk = self.connection.recv(65536)
            if not chunk:
                raise TrialError("Unity disconnected before confirmed completion")
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\n", 1)
        return copy_json_object(json.loads(line), name="platform message")

    def wait_for_player_exit(self) -> None:
        deadline = time.monotonic() + SHUTDOWN_TIMEOUT
        disconnected = False
        while self.player.poll() is None:
            if time.monotonic() >= deadline:
                raise TrialError("Unity did not exit after the saved acknowledgement")
            if disconnected:
                time.sleep(0.1)
            elif select.select([self.connection], [], [], 0.1)[0]:
                if self.connection.recv(65536):
                    raise TrialError("Unexpected data after trial completion")
                disconnected = True
        # A fast exit can leave unread bytes even when poll already reports completion.
        if not disconnected and select.select([self.connection], [], [], 0)[0]:
            if self.connection.recv(65536):
                raise TrialError("Unexpected data after trial completion")
        if self.player.returncode != 0:
            raise TrialError(f"Unity exited with code {self.player.returncode}")


def collect_reports(channel: TrialConnection, assignment: dict, stream) -> None:
    expected = assignment["episodes_per_trial"]
    attempts = set()
    completion_deadline = None
    while True:
        message = channel.receive(completion_deadline)
        if message.get("type") == "trial_complete":
            if (message.get("suite_id") != assignment["suite_id"]
                    or message.get("trial_id") != assignment["trial_id"]
                    or type(message.get("completed_episodes")) is not int
                    or message["completed_episodes"] != expected
                    or len(attempts) != expected):
                raise TrialError("Completion identity/count does not match the saved episodes")
            if channel.buffer:
                raise TrialError("Unexpected data after trial completion")
            # The acknowledgement promises that the complete file is on disk and closed.
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            channel.send({
                "type": "trial_saved",
                "suite_id": assignment["suite_id"],
                "trial_id": assignment["trial_id"],
                "saved_episodes": len(attempts),
            })
            return
        if "type" in message:
            raise TrialError("Unexpected platform control message")
        report = UnitReport.from_dict(message)
        if report.attempt_id in attempts:
            raise TrialError(f"Duplicate attempt ID: {report.attempt_id}")
        if len(attempts) >= expected:
            raise TrialError("Received more episodes than assigned")
        record = {
            "suite_id": assignment["suite_id"],
            "trial_id": assignment["trial_id"],
            "environment_id": assignment["environment"]["id"],
            "agent_id": assignment["agent"]["id"],
            "episode_index": len(attempts) + 1,
            "report": report.to_dict(),
        }
        stream.write(json.dumps(record, allow_nan=False) + "\n")
        stream.flush()
        attempts.add(report.attempt_id)
        if len(attempts) == expected:
            completion_deadline = time.monotonic() + COMPLETION_TIMEOUT


def stop_player(player: subprocess.Popen) -> None:
    if player.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(player.pid, signal.SIGTERM)
        else:
            player.terminate()
        player.wait(timeout=TERMINATE_TIMEOUT)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(player.pid, signal.SIGKILL)
        else:
            player.kill()
        player.wait()
    except ProcessLookupError:
        player.wait()


def run_trial(assignment_path: Path, output_dir: Path, command: list[str]) -> None:
    assignment = load_assignment(assignment_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    # Exclusive creation avoids mixing attempts from separate invocations.
    with (output_dir / "episodes.jsonl").open("x", encoding="utf-8") as stream:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            session_id = uuid4().hex
            env = dict(os.environ)
            env.update({
                "QA_TRIAL_CONFIG": str(assignment_path.resolve()),
                "QA_OUTPUT_DIR": str(output_dir.resolve()),
                "QA_REPORT_PORT": str(listener.getsockname()[1]),
                "QA_RECORDER_SESSION": session_id,
            })
            options = {}
            if os.name == "posix":
                options["start_new_session"] = True
            elif os.name == "nt":
                options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            player = None
            try:
                player = subprocess.Popen(command, env=env, **options)
                deadline = time.monotonic() + STARTUP_TIMEOUT
                while not select.select([listener], [], [], 0.1)[0]:
                    if player.poll() is not None:
                        raise TrialError(f"Unity exited before connecting (code {player.returncode})")
                    if time.monotonic() >= deadline:
                        raise TrialError("Unity did not connect within the startup timeout")
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(COMPLETION_TIMEOUT)
                    channel = TrialConnection(connection, player)
                    channel.send({
                        "type": "ready",
                        "schema_version": EVALUATION_SCHEMA_VERSION,
                        "platform_protocol_version": PLATFORM_PROTOCOL_VERSION,
                        "process_id": os.getpid(),
                        "session_id": session_id,
                        "suite_id": assignment["suite_id"],
                        "trial_id": assignment["trial_id"],
                        "episodes_per_trial": assignment["episodes_per_trial"],
                    })
                    collect_reports(channel, assignment, stream)
                    channel.wait_for_player_exit()
                print(f"Trial {assignment['trial_id']}: saved {assignment['episodes_per_trial']} episodes", flush=True)
            finally:
                # A second signal must not leave Unity running during cleanup.
                handlers = {}
                for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
                    if hasattr(signal, name):
                        sig = getattr(signal, name)
                        handlers[sig] = signal.signal(sig, signal.SIG_IGN)
                try:
                    if player is not None:
                        stop_player(player)
                finally:
                    for sig, handler in handlers.items():
                        signal.signal(sig, handler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- Unity executable and its arguments")
    args = parser.parse_args()
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("provide -- followed by the Unity executable and its arguments")
    for key in ("QA_TRIAL_CONFIG", "QA_OUTPUT_DIR"):
        if not os.environ.get(key):
            parser.error(f"{key} is required")

    def cancel(signum, frame):
        raise KeyboardInterrupt

    handlers = {}
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            sig = getattr(signal, name)
            handlers[sig] = signal.signal(sig, cancel)
    try:
        run_trial(Path(os.environ["QA_TRIAL_CONFIG"]), Path(os.environ["QA_OUTPUT_DIR"]), command)
        return 0
    except KeyboardInterrupt:
        print("Trial cancelled", file=sys.stderr, flush=True)
        return 130
    except (OSError, ValueError, TypeError, KeyError, TrialError) as error:
        print(f"Trial failed: {error}", file=sys.stderr, flush=True)
        return 1
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    sys.exit(main())
