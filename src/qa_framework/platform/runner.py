"""Launch fresh trial containers with a bounded, lazily consumed assignment queue."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import count
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from uuid import uuid4

from qa_framework.platform.config import SuiteConfig
from qa_framework.platform.preparation import PreparationError, prepare_models


class DockerError(RuntimeError):
    """A Docker CLI operation failed."""


@dataclass(slots=True)
class Trial:
    trial_id: str
    directory: Path
    environment_id: str
    agent_id: str
    model_path: Path
    container_id: str | None = None

    def context(self) -> dict[str, object]:
        return {
            "trial_id": self.trial_id,
            "environment_id": self.environment_id,
            "agent_id": self.agent_id,
            "container_id": self.container_id,
        }


class Docker:
    def __init__(self, suite_id: str, suite_dir: Path, image: str) -> None:
        self.suite_id = suite_id
        # pin compose file -> skip implicit .env
        self.compose = [
            "compose", "--project-name", suite_id,
            "--env-file", os.devnull,
            "--file", str(Path(__file__).with_name("compose.yaml")),
        ]

        # clear inherited compose overrides
        self.env = dict(os.environ)
        for key in list(self.env):
            if key.startswith("COMPOSE_"):
                del self.env[key]

        # mount paths + trial image
        self.env.update({
            "QA_TRIAL_IMAGE": image,
            "QA_TRIAL_CONFIG_HOST": str(suite_dir / "assignment.json"),
            "QA_OUTPUT_DIR_HOST": str(suite_dir),
            "QA_MODEL_HOST": str(suite_dir / "model.sentis"),
        })

    def command(self, *args: str, log_path: Path | None = None) -> str:
        try:
            # small output -> capture in memory
            if log_path is None:
                result = subprocess.run(
                    ["docker", *args], env=self.env, capture_output=True,
                    text=True, encoding="utf-8", errors="replace", check=False,
                )
                if result.returncode:
                    raise DockerError(result.stderr.strip() or result.stdout.strip())
                return result.stdout.strip()

            # large logs -> stream straight to file
            with log_path.open("wb") as stream:
                result = subprocess.run(
                    ["docker", *args], env=self.env, stdout=stream,
                    stderr=subprocess.STDOUT, check=False,
                )
            if result.returncode:
                raise DockerError(f"docker {' '.join(args)} failed; see {log_path}")
            return ""
        except OSError as error:
            raise DockerError(str(error)) from error

    def check_available(self) -> None:
        self.command("info", "--format", "{{.ServerVersion}}")
        self.command("compose", "version")

    def launch(self, trial: Trial) -> str:
        # per-trial mounts -> start fresh container
        self.env["QA_TRIAL_CONFIG_HOST"] = str(trial.directory / "assignment.json")
        self.env["QA_OUTPUT_DIR_HOST"] = str(trial.directory / "output")
        self.env["QA_MODEL_HOST"] = str(trial.model_path)
        return self.command(
            *self.compose, "run", "--detach", "--no-deps",
            "--name", trial.trial_id, "trial",
        )

    def inspect(self, *container_ids: str) -> list[dict]:
        return json.loads(self.command("container", "inspect", *container_ids))

    def owned_containers(self, name: str | None = None) -> list[str]:
        args = [
            "container", "ls", "--all", "--quiet", "--no-trunc",
            "--filter", f"label=com.docker.compose.project={self.suite_id}",
        ]

        # optional name filter for one trial
        if name is not None:
            args.extend(["--filter", f"name=^/{name}$"])
        return self.command(*args).split()


class RunManager:
    def __init__(self, config: SuiteConfig) -> None:
        self.config = config
        self.suite_id = f"qa-suite-{uuid4().hex}"
        self.directory = config.output_dir / self.suite_id
        self.docker = Docker(self.suite_id, self.directory, config.image)
        self.active: dict[str, Trial] = {}
        self.completed = 0
        self.failed = 0
        self.prepared_models: dict[str, Path] = {}

    def record(self, event: str, **details: object) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "suite_id": self.suite_id,
            "event": event,
            **details,
        }
        with (self.directory / "status.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, allow_nan=False) + "\n")

    def assignments(self):
        sequence = count(1)
        # environments -> agents -> one assignment per pair
        for environment in self.config.environments:
            for agent in self.config.agents:
                trial_id = f"{self.suite_id}-trial-{next(sequence):06d}"
                directory = self.directory / "trials" / trial_id
                yield Trial(trial_id, directory, environment["id"], agent["id"], self.prepared_models[agent["model"]]), {
                    "suite_id": self.suite_id,
                    "trial_id": trial_id,
                    "episodes_per_trial": self.config.episodes_per_trial,
                    "environment": environment,
                    "agent": {**agent, "model": "/qa/model/agent.sentis"},
                }

    def launch(self, trial: Trial, assignment: dict) -> None:
        # save trial config before launch
        (trial.directory / "output").mkdir(parents=True)
        (trial.directory / "assignment.json").write_text(
            json.dumps(assignment, indent=2, allow_nan=False) + "\n", encoding="utf-8",
        )

        # launch -> track container on success
        self.record("trial_launching", **trial.context())
        try:
            trial.container_id = self.docker.launch(trial)
        except DockerError as error:
            # save launch failure
            (trial.directory / "launch.log").write_text(str(error) + "\n", encoding="utf-8")
            self.record("trial_launch_failed", **trial.context(), error=str(error))
            self.failed += 1

            # failed command can leave container -> stop -> save logs -> remove
            self.docker.check_available()
            for container_id in self.docker.owned_containers(trial.trial_id):
                self.docker.command("container", "stop", container_id)
                state = self.docker.inspect(container_id)[0]["State"]
                self.docker.command("container", "logs", container_id, log_path=trial.directory / "container.log")
                self.record("trial_stopped", **trial.context(), exit_code=state["ExitCode"])
                self.docker.command("container", "rm", container_id)
            return

        self.active[trial.container_id] = trial
        self.record("trial_started", **trial.context())

    def collect_exits(self) -> None:
        if not self.active:
            return

        # inspect active containers -> skip anything still running
        states = self.docker.inspect(*self.active)
        for container in states:
            state = container["State"]
            if state["Status"] not in ("exited", "dead"):
                continue

            # exited -> save logs -> remove container -> free slot
            container_id = container["Id"]
            trial = self.active[container_id]
            self.docker.command("container", "logs", container_id, log_path=trial.directory / "container.log")
            self.record("trial_exited", **trial.context(), exit_code=state["ExitCode"])
            self.docker.command("container", "rm", container_id)
            del self.active[container_id]

            # count completion separately from failure
            self.completed += 1
            if state["ExitCode"] != 0:
                self.failed += 1

    def cleanup(self) -> None:
        # suite label catches interrupted launches too; keep output files
        container_ids = self.docker.owned_containers()
        try:
            if container_ids:
                # stop -> inspect final state -> save logs
                self.docker.command("container", "stop", *container_ids)
                for container in self.docker.inspect(*container_ids):
                    container_id = container["Id"]
                    trial_dir = self.directory / "trials" / container["Name"].lstrip("/")
                    self.docker.command("container", "logs", container_id, log_path=trial_dir / "container.log")
                    self.record(
                        "trial_stopped", trial_id=trial_dir.name, container_id=container_id,
                        exit_code=container["State"]["ExitCode"],
                    )
        finally:
            # log failure still needs cleanup -> remove containers -> compose down
            try:
                if container_ids:
                    self.docker.command("container", "rm", "--force", *container_ids)
            finally:
                self.docker.command(*self.docker.compose, "down", "--remove-orphans")

    # suite setup -> fill slots -> collect exits -> cleanup -> final status
    def run(self) -> int:
        # suite output + expected trial count
        self.directory.mkdir(parents=True)
        print(f"Suite {self.suite_id}: {self.directory}", flush=True)
        total = len(self.config.environments) * len(self.config.agents)
        self.record("suite_started", total_trials=total, max_concurrent_trials=self.config.max_concurrent_trials)

        # track how the suite ends
        cancelled = False
        infrastructure_failed = False
        docker_checked = False

        # check docker before consuming assignments
        try:
            self.record("model_preparation_started")
            self.prepared_models = prepare_models(self.config, self.directory)
            self.record("model_preparation_finished", distinct_models=len(self.prepared_models))
            self.docker.check_available()
            docker_checked = True
            assignments = iter(self.assignments())
            exhausted = False

            # keep going until queue empty + no active trials
            while not exhausted or self.active:
                # fill run slots with new trials
                while not exhausted and len(self.active) < self.config.max_concurrent_trials:
                    assignment = next(assignments, None)

                    # queue empty -> stop filling slots
                    if assignment is None:
                        exhausted = True
                    else:
                        # next pair -> launch trial
                        self.launch(*assignment)

                # wait -> collect finished containers -> free slots
                if self.active:
                    time.sleep(1)
                    self.collect_exits()

        # interrupted -> mark cancelled before cleanup
        except KeyboardInterrupt:
            cancelled = True
            self.record("suite_cancelled")

        # infrastructure failure -> stop scheduling
        except (DockerError, PreparationError, OSError, ValueError) as error:
            infrastructure_failed = True
            self.record("infrastructure_failed", error=str(error))
            print(f"Infrastructure failure: {error}", flush=True)
        finally:
            # ignore stop signals while cleanup runs
            previous_handlers = {}
            for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
                if hasattr(signal, name):
                    sig = getattr(signal, name)
                    previous_handlers[sig] = signal.signal(sig, signal.SIG_IGN)

            # docker checked -> clean suite resources
            try:
                if docker_checked:
                    self.cleanup()
            except (DockerError, OSError, ValueError) as error:
                infrastructure_failed = True
                self.record("cleanup_failed", error=str(error))
                print(f"Cleanup failed for {self.suite_id}: {error}", flush=True)
            finally:
                # cleanup done -> restore signal handlers
                for sig, handler in previous_handlers.items():
                    signal.signal(sig, handler)

        # cancelled -> 130; failure or missing trial -> 1; otherwise -> 0
        exit_code = 0
        if cancelled:
            exit_code = 130
        elif infrastructure_failed or self.failed or self.completed != total:
            exit_code = 1

        # persist final counts + process exit code
        self.record(
            "suite_finished", exit_code=exit_code, completed_trials=self.completed,
            failed_trials=self.failed, total_trials=total,
        )
        return exit_code
