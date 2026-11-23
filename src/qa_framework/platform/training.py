"""Own a Unity training Player and publish a resumable checkpoint plus ONNX."""

from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from uuid import uuid4

from qa_framework.platform.config import trainer_settings
from qa_framework.platform.preparation import validate_onnx
from qa_framework.platform.trial import STARTUP_TIMEOUT, stop_player
from qa_framework.schema.json_data import copy_json_object


def resolve_trainer(settings: dict, checkpoint: str | None) -> dict:
    """Inherit saved PPO settings and reject overrides the library would ignore."""
    config = trainer_settings(settings, resuming=checkpoint is not None)
    if checkpoint is None:
        return config
    from stable_baselines3 import PPO
    from stable_baselines3.common.policies import ActorCriticPolicy

    saved = PPO.load(checkpoint, device="cpu")
    if type(saved.policy) is not ActorCriticPolicy:
        raise ValueError("only feedforward PPO MlpPolicy checkpoints are supported")
    inherited = {"learning_rate": saved.learning_rate, "gamma": saved.gamma,
                 "n_steps": saved.n_steps, "ppo": {"batch_size": saved.batch_size}}
    for key, value in inherited.items():
        if key in config and config[key] != value:
            raise ValueError(f"resumed trainer.{key} conflicts with the checkpoint; only ent_coef may override saved training settings")
        config[key] = value
    if "ent_coef" not in config:
        config["ent_coef"] = saved.ent_coef
    # Reject unsupported saved schedules/settings before starting the Player.
    return trainer_settings(config, resuming=True)


def run_training(assignment_path: Path, output_dir: Path, command: list[str]) -> None:
    import yaml

    assignment = copy_json_object(json.loads(assignment_path.read_text(encoding="utf-8")), name="training assignment")
    for key in ("run_id", "node_id"):
        if not isinstance(assignment.get(key), str) or not assignment[key]:
            raise ValueError(f"training assignment requires {key}")
    checkpoint = assignment.get("checkpoint")
    if checkpoint is not None and (not isinstance(checkpoint, str) or not Path(checkpoint).is_file()):
        raise ValueError("checkpoint must name an existing file")
    config = resolve_trainer(assignment.get("trainer"), checkpoint)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if (output_dir / "artifacts.json").exists() or (output_dir / "training.yaml").exists():
        raise ValueError("training requires a fresh output directory")

    # The image supplies the existing library; do not vendor or fork its trainer.
    library = Path(os.environ.get("QA_RL_PYTHON", "/opt/unity-rl/PythonEnv"))
    if not (library / "train.py").is_file() or not (library / "export_onnx.py").is_file():
        raise ValueError("QA_RL_PYTHON must name the Unity RL library's PythonEnv directory")
    sys.path.insert(0, str(library.resolve()))
    trainer = importlib.import_module("train")
    exporter = importlib.import_module("export_onnx")

    session = uuid4().hex
    ready_path = output_dir / f"ready-{session}.json"
    env = dict(os.environ)
    env.update({"QA_TRAIN_CONFIG": str(assignment_path.resolve()), "QA_OUTPUT_DIR": str(output_dir),
                "QA_TRAIN_READY": str(ready_path), "QA_TRAIN_SESSION": session})
    options = {}
    if os.name == "posix":
        options["start_new_session"] = True
    elif os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    player = None
    try:
        player = subprocess.Popen(command, env=env, **options)
        deadline = time.monotonic() + STARTUP_TIMEOUT
        # A TCP readiness probe would consume RlTrainingHost's single connection.
        while not ready_path.is_file():
            if player.poll() is not None:
                raise RuntimeError(f"Unity exited before training readiness (code {player.returncode})")
            if time.monotonic() >= deadline:
                raise RuntimeError("Unity did not publish training readiness within 30 seconds")
            time.sleep(0.1)
        ready = copy_json_object(json.loads(ready_path.read_text(encoding="utf-8-sig")), name="training readiness")
        if (ready.get("session_id") != session or ready.get("node_id") != assignment["node_id"]
                or type(ready.get("port")) is not int or not 1 <= ready["port"] <= 65535):
            raise ValueError("Unity training readiness does not match this assignment")
        config.update({"port": ready["port"], "output_dir": str(output_dir / "training"), "resume_from": checkpoint,
                       "metrics_dir": str(output_dir)})
        config_path = output_dir / "training.yaml"
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        final_checkpoint = trainer.train(config_path)
        if player.poll() is not None:
            raise RuntimeError(f"Unity exited during training (code {player.returncode})")
        shutil.copyfile(final_checkpoint, output_dir / "checkpoint.zip")
        exporter.export_onnx(output_dir / "checkpoint.zip", output_dir / "model.onnx")
        validate_onnx(output_dir / "model.onnx")
    finally:
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

    # Publish only after training, export validation, and Player cleanup succeed.
    manifest = {"run_id": assignment["run_id"], "node_id": assignment["node_id"],
                "parent": assignment.get("parent"), "environment": assignment["environment"],
                "trainer": config, "outputs": {"checkpoint": "checkpoint.zip", "onnx": "model.onnx"}}
    if (output_dir / "progress.json").is_file():
        manifest["metrics"] = "progress.json"
    temporary = output_dir / "artifacts.tmp"
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(output_dir / "artifacts.json")
    print(f"Training {assignment['node_id']}: published checkpoint.zip and model.onnx", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- Unity executable and its arguments")
    args = parser.parse_args()
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("provide -- followed by the Unity executable and its arguments")
    for key in ("QA_TRAIN_CONFIG", "QA_OUTPUT_DIR"):
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
        run_training(Path(os.environ["QA_TRAIN_CONFIG"]), Path(os.environ["QA_OUTPUT_DIR"]), command)
        return 0
    except KeyboardInterrupt:
        print("Training cancelled", file=sys.stderr, flush=True)
        return 130
    except Exception as error:
        print(f"Training failed: {error}", file=sys.stderr, flush=True)
        return 1
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    sys.exit(main())
