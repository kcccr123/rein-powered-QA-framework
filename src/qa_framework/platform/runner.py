"""Schedule training nodes and lazy trial matrices under one container limit."""

from __future__ import annotations

from collections import deque
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Iterator
from uuid import uuid4
import zipfile

from qa_framework.platform.config import DAGConfig, NodeConfig
from qa_framework.platform.finalizer import collect_dataset, read_object, skip_analysis, write_json
from qa_framework.platform.preparation import PreparationError, conversion_project, prepare_models, validate_onnx


class DockerError(RuntimeError):
    """A Docker CLI operation failed."""


@dataclass(slots=True)
class Job:
    id: str
    node_id: str
    kind: str
    directory: Path
    image: str
    assignment: dict
    mounts: dict[str, Path] = field(default_factory=dict)
    container_id: str | None = None

    def context(self) -> dict:
        return {"job_id": self.id, "node_id": self.node_id, "kind": self.kind,
                "container_id": self.container_id}


@dataclass(slots=True)
class NodeState:
    config: NodeConfig
    directory: Path
    status: str = "pending"
    error: str | None = None
    jobs: Iterator[Job] | None = None
    exhausted: bool = False
    total: int = 1
    completed: int = 0
    failed: int = 0
    outputs: dict[str, Path] = field(default_factory=dict)


class Docker:
    def __init__(self, run_id: str, directory: Path, image: str) -> None:
        self.run_id = run_id
        self.compose = [
            "compose", "--project-name", run_id, "--env-file", os.devnull,
            "--file", str(Path(__file__).with_name("compose.yaml")),
        ]
        self.env = dict(os.environ)
        for key in list(self.env):
            if key.startswith("COMPOSE_"):
                del self.env[key]
        self.env.update({
            "QA_JOB_IMAGE": image,
            "QA_CONFIG_HOST": str(directory / "assignment.json"),
            "QA_OUTPUT_DIR_HOST": str(directory),
        })

    def command(self, *args: str, log_path: Path | None = None) -> str:
        try:
            if log_path is None:
                result = subprocess.run(
                    ["docker", *args], env=self.env, capture_output=True,
                    text=True, encoding="utf-8", errors="replace", check=False,
                )
                if result.returncode:
                    raise DockerError(result.stderr.strip() or result.stdout.strip())
                return result.stdout.strip()
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

    def launch(self, job: Job) -> str:
        self.env["QA_JOB_IMAGE"] = job.image
        self.env["QA_CONFIG_HOST"] = str(job.directory / "assignment.json")
        self.env["QA_OUTPUT_DIR_HOST"] = str(job.directory / "output")
        # Override only the selected artifact mounts. Scratch training needs none.
        volumes = [{"type": "bind", "source": str(source), "target": target,
                    "read_only": True, "bind": {"create_host_path": False}}
                   for target, source in job.mounts.items()]
        override = job.directory / "mounts.json"
        write_json(override, {"services": {"job": {"volumes": volumes}}})
        return self.command(
            *self.compose, "--file", str(override), "run", "--detach", "--no-deps",
            "--name", job.id, "job",
        )

    def inspect(self, *container_ids: str) -> list[dict]:
        return json.loads(self.command("container", "inspect", *container_ids))

    def owned_containers(self, name: str | None = None) -> list[str]:
        args = [
            "container", "ls", "--all", "--quiet", "--no-trunc",
            "--filter", f"label=com.docker.compose.project={self.run_id}",
        ]
        if name is not None:
            args.extend(["--filter", f"name=^/{name}$"])
        return self.command(*args).split()


class RunManager:
    def __init__(self, config: DAGConfig) -> None:
        self.config = config
        self.run_id = f"qa-run-{uuid4().hex}"
        self.directory = config.output_dir / self.run_id
        self.docker = Docker(self.run_id, self.directory, config.nodes[0].image)
        self.nodes = {node.id: NodeState(node, self.directory / "nodes" / node.id) for node in config.nodes}
        self.ready: deque[str] = deque()
        self.active: dict[str, Job] = {}
        self.launched: dict[str, Job] = {}
        self.prepared_models: dict[str, Path] = {}
        self.preparation_failures: dict[str, str] = {}
        self.preparation_context = ExitStack()
        self.conversion_directory: Path | None = None

    def record(self, event: str, **details: object) -> None:
        record = {"timestamp": datetime.now(timezone.utc).isoformat(),
                  "run_id": self.run_id, "event": event, **details}
        with (self.directory / "status.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, allow_nan=False) + "\n")

    def save_node(self, state: NodeState) -> None:
        write_json(state.directory / "manifest.json", {
            "run_id": self.run_id, "node_id": state.config.id, "type": state.config.type,
            "status": state.status, "error": state.error, "image": state.config.image,
            "dependencies": sorted(state.config.dependencies), "config": state.config.settings,
            "total_jobs": state.total, "completed_jobs": state.completed, "failed_jobs": state.failed,
            "outputs": {key: str(path.relative_to(state.directory)) for key, path in state.outputs.items()},
            "jobs_file": "jobs.jsonl",
        })

    def set_status(self, state: NodeState, status: str, error: str | None = None) -> None:
        state.status = status
        state.error = error
        self.record("node_" + status, node_id=state.config.id, error=error)
        self.save_node(state)

    def resolve_input(self, value: str | dict) -> Path:
        if isinstance(value, str):
            path = Path(value)
        else:
            path = self.nodes[value["node"]].outputs[value["output"]]
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"missing or empty input artifact: {path}")
        return path

    def training_jobs(self, state: NodeState) -> Iterator[Job]:
        settings = state.config.settings
        assignment = {"run_id": self.run_id, "node_id": state.config.id,
                      "environment": settings["environment"], "trainer": settings["trainer"],
                      "checkpoint": None, "parent": settings.get("checkpoint")}
        mounts = {}
        if settings.get("checkpoint") is not None:
            mounts["/qa/input/checkpoint.zip"] = self.resolve_input(settings["checkpoint"])
            assignment["checkpoint"] = "/qa/input/checkpoint.zip"
        yield Job(f"{self.run_id}-{state.config.id}", state.config.id, "train",
                  state.directory, state.config.image, assignment, mounts)

    def suite_jobs(self, state: NodeState, models: dict[str, Path]) -> Iterator[Job]:
        settings = state.config.settings
        suite_id = f"{self.run_id}-{state.config.id}"
        sequence = 0
        for environment in settings["environments"]:
            for agent in settings["agents"]:
                sequence += 1
                trial_id = f"{suite_id}-trial-{sequence:06d}"
                yield Job(
                    trial_id, state.config.id, "trial", state.directory / "trials" / trial_id,
                    state.config.image, {
                        "run_id": self.run_id, "node_id": state.config.id,
                        "suite_id": suite_id, "trial_id": trial_id,
                        "episodes_per_trial": settings["episodes_per_trial"],
                        "environment": environment,
                        "agent": {**agent, "model": "/qa/model/agent.sentis"},
                        "model_source": agent["model"],
                    }, {"/qa/model/agent.sentis": models[agent["id"]]},
                )

    def activate_finalizer(self, state: NodeState) -> None:
        if any(node.config.type != "finalizer" and node.status in ("pending", "running")
               for node in self.nodes.values()):
            return
        self.set_status(state, "running")
        try:
            settings = state.config.settings
            groups = {kind: [self.nodes[node_id].directory for node_id in settings[kind]["nodes"]]
                      for kind in ("training", "testing") if kind in settings}
            scripts = {kind: settings[kind]["scripts"] for kind in groups}
            dataset = state.directory / "dataset"
            output = state.directory / "output"
            report = output / "report.json"
            index = collect_dataset(dataset, groups)
            state.outputs = {path.stem: path for path in sorted(dataset.iterdir())}
            if not index["complete"]:
                reason = "selected results are incomplete; see dataset/index.json"
                skip_analysis(output, scripts, reason)
                state.outputs["report"] = report
                blocked = any(self.nodes[node_id].status != "succeeded" for node_id in state.config.dependencies)
                self.set_status(state, "blocked" if blocked else "failed", reason)
                return
            if not any(scripts.values()):
                output.mkdir()
                write_json(report, {"status": "succeeded", "inputs_complete": True, "scripts": []})
                state.outputs["report"] = report
                self.set_status(state, "succeeded")
                return
            mounts = {"/qa/dataset": dataset}
            for node_id in sorted(state.config.dependencies):
                mounts[f"/qa/upstream/{node_id}"] = self.nodes[node_id].directory
            assignment = {"run_id": self.run_id, "node_id": state.config.id, "scripts": scripts}
            job = Job(f"{self.run_id}-{state.config.id}", state.config.id, "finalizer",
                      state.directory, state.config.image, assignment, mounts)
            state.jobs = iter((job,))
            self.ready.append(state.config.id)
        except (OSError, KeyError, TypeError, ValueError) as error:
            self.set_status(state, "failed", str(error))

    def activate_nodes(self) -> None:
        # YAML order breaks ties; a deque gives each ready node one slot per turn.
        for state in self.nodes.values():
            if state.status != "pending":
                continue
            if state.config.type == "finalizer":
                self.activate_finalizer(state)
                continue
            parents = [self.nodes[key] for key in state.config.dependencies]
            if any(parent.status in ("failed", "blocked", "cancelled") for parent in parents):
                self.set_status(state, "blocked", "a required upstream node did not succeed")
                continue
            if any(parent.status != "succeeded" for parent in parents):
                continue
            self.set_status(state, "running")
            try:
                if state.config.type == "train":
                    # Resolve inputs now, before the lazy iterator is consumed.
                    if state.config.settings.get("checkpoint") is not None:
                        self.resolve_input(state.config.settings["checkpoint"])
                    state.jobs = self.training_jobs(state)
                else:
                    paths = {agent["id"]: str(self.resolve_input(agent["model"]))
                             for agent in state.config.settings["agents"]}
                    for path in dict.fromkeys(paths.values()):
                        if path in self.preparation_failures:
                            raise PreparationError(self.preparation_failures[path])
                        if path in self.prepared_models:
                            continue
                        output = self.directory / "models" / f"conversion-{len(self.prepared_models) + len(self.preparation_failures) + 1:04d}"
                        self.record("model_preparation_started", node_id=state.config.id, source=path)
                        try:
                            if self.conversion_directory is None:
                                self.conversion_directory = self.preparation_context.enter_context(conversion_project())
                            prepared = prepare_models(self.config, output, [path], self.conversion_directory)
                        except (PreparationError, OSError, ValueError) as error:
                            self.preparation_failures[path] = str(error)
                            raise
                        self.prepared_models.update(prepared)
                        self.record("model_preparation_finished", source=path, artifact=str(prepared[path]))
                    models = {agent_id: self.prepared_models[path] for agent_id, path in paths.items()}
                    state.jobs = self.suite_jobs(state, models)
                self.ready.append(state.config.id)
            except (PreparationError, OSError, ValueError) as error:
                self.set_status(state, "failed", str(error))

    def finish_job(self, job: Job, exit_code: int | None, error: str | None = None) -> None:
        state = self.nodes[job.node_id]
        state.completed += 1
        if exit_code != 0 and error is None:
            error = f"container exited with code {exit_code}"
        if error is None and job.kind == "train":
            try:
                output = job.directory / "output"
                manifest = json.loads((output / "artifacts.json").read_text(encoding="utf-8"))
                if (not isinstance(manifest, dict) or manifest.get("run_id") != self.run_id or manifest.get("node_id") != job.node_id
                        or manifest.get("outputs") != {"checkpoint": "checkpoint.zip", "onnx": "model.onnx"}):
                    raise ValueError("training artifact manifest does not match this node")
                checkpoint = output / "checkpoint.zip"
                if not zipfile.is_zipfile(checkpoint):
                    raise ValueError("training did not publish a checkpoint ZIP")
                validate_onnx(output / "model.onnx")
                state.outputs = {"checkpoint": checkpoint, "onnx": output / "model.onnx"}
            except (OSError, ValueError, PreparationError) as failure:
                error = str(failure)
        if job.kind == "finalizer":
            report = job.directory / "output" / "report.json"
            try:
                outcome = read_object(report)
                state.outputs["report"] = report
                if error is None and outcome.get("status") != "succeeded":
                    error = "finalizer did not report successful script execution"
            except (OSError, TypeError, ValueError) as failure:
                if error is None:
                    error = f"missing or invalid finalizer report: {failure}"
        if error is not None:
            state.failed += 1
        record = {**job.context(), "exit_code": exit_code, "error": error,
                  "directory": str(job.directory.relative_to(state.directory)),
                  "assignment": job.assignment}
        with (state.directory / "jobs.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, allow_nan=False) + "\n")
        self.record("job_finished", **job.context(), exit_code=exit_code, error=error)
        self.save_node(state)

    def launch(self, job: Job) -> None:
        (job.directory / "output").mkdir(parents=True, exist_ok=True)
        write_json(job.directory / "assignment.json", job.assignment)
        self.launched[job.id] = job
        self.record("job_launching", **job.context())
        try:
            job.container_id = self.docker.launch(job)
        except DockerError as error:
            (job.directory / "launch.log").write_text(str(error) + "\n", encoding="utf-8")
            # A failed CLI invocation can still have created a container.
            self.docker.check_available()
            for container_id in self.docker.owned_containers(job.id):
                self.docker.command("container", "stop", container_id)
                self.docker.command("container", "logs", container_id, log_path=job.directory / "container.log")
                self.docker.command("container", "rm", container_id)
            self.finish_job(job, None, str(error))
            del self.launched[job.id]
            return
        self.active[job.container_id] = job
        self.record("job_started", **job.context())

    def collect_exits(self) -> None:
        if not self.active:
            return
        for container in self.docker.inspect(*self.active):
            state = container["State"]
            if state["Status"] not in ("exited", "dead"):
                continue
            job = self.active[container["Id"]]
            self.docker.command("container", "logs", job.container_id, log_path=job.directory / "container.log")
            self.docker.command("container", "rm", job.container_id)
            del self.active[job.container_id]
            self.finish_job(job, state["ExitCode"])
            del self.launched[job.id]

    def complete_nodes(self) -> None:
        for state in self.nodes.values():
            if state.status == "running" and state.exhausted and state.completed == state.total:
                if state.failed:
                    self.set_status(state, "failed", f"{state.failed} of {state.total} jobs failed")
                else:
                    self.set_status(state, "succeeded")

    def cleanup(self) -> None:
        container_ids = self.docker.owned_containers()
        try:
            if container_ids:
                self.docker.command("container", "stop", *container_ids)
                for container in self.docker.inspect(*container_ids):
                    job = self.launched.get(container["Name"].lstrip("/"))
                    if job is not None:
                        self.docker.command("container", "logs", container["Id"], log_path=job.directory / "container.log")
                        self.record("job_stopped", **job.context(), exit_code=container["State"]["ExitCode"])
        finally:
            try:
                if container_ids:
                    self.docker.command("container", "rm", "--force", *container_ids)
            finally:
                self.docker.command(*self.docker.compose, "down", "--remove-orphans")

    def run(self) -> int:
        self.directory.mkdir(parents=True)
        (self.directory / "models").mkdir()
        for state in self.nodes.values():
            state.directory.mkdir(parents=True)
            if state.config.type == "suite":
                settings = state.config.settings
                state.total = len(settings["environments"]) * len(settings["agents"])
            elif state.config.type == "finalizer":
                state.total = int(any(state.config.settings.get(group, {}).get("scripts")
                                      for group in ("training", "testing")))
            self.save_node(state)
        print(f"Run {self.run_id}: {self.directory}", flush=True)
        self.record("run_started", max_concurrent_containers=self.config.max_concurrent_containers)
        cancelled = False
        infrastructure_failed = False
        docker_checked = False
        try:
            self.docker.check_available()
            docker_checked = True
            while any(state.status in ("pending", "running") for state in self.nodes.values()):
                self.activate_nodes()
                while self.ready and len(self.active) < self.config.max_concurrent_containers:
                    state = self.nodes[self.ready.popleft()]
                    job = next(state.jobs, None)
                    if job is None:
                        state.exhausted = True
                    else:
                        self.launch(job)
                        self.ready.append(state.config.id)
                if self.active:
                    time.sleep(1)
                    self.collect_exits()
                self.complete_nodes()
        except KeyboardInterrupt:
            cancelled = True
            self.record("run_cancelled")
        except (DockerError, OSError, ValueError) as error:
            infrastructure_failed = True
            self.record("infrastructure_failed", error=str(error))
            print(f"Infrastructure failure: {error}", flush=True)
        finally:
            handlers = {}
            for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
                if hasattr(signal, name):
                    sig = getattr(signal, name)
                    handlers[sig] = signal.signal(sig, signal.SIG_IGN)
            try:
                try:
                    if docker_checked:
                        self.cleanup()
                finally:
                    self.preparation_context.close()
            except (DockerError, OSError, ValueError) as error:
                infrastructure_failed = True
                self.record("cleanup_failed", error=str(error))
                print(f"Cleanup failed for {self.run_id}: {error}", flush=True)
            finally:
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)

        # Every declared node receives a terminal status, even after interruption.
        for state in self.nodes.values():
            if state.status in ("pending", "running"):
                if cancelled:
                    self.set_status(state, "cancelled", "run cancelled")
                elif state.status == "running":
                    self.set_status(state, "failed", "run infrastructure failed")
                else:
                    self.set_status(state, "blocked", "run infrastructure failed")
        exit_code = 0
        if cancelled:
            exit_code = 130
        elif infrastructure_failed or any(state.status != "succeeded" for state in self.nodes.values()):
            exit_code = 1
        write_json(self.directory / "manifest.json", {
            "run_id": self.run_id, "exit_code": exit_code, "cancelled": cancelled,
            "infrastructure_failed": infrastructure_failed,
            "nodes": [{"id": state.config.id, "type": state.config.type, "status": state.status,
                       "manifest": f"nodes/{state.config.id}/manifest.json"} for state in self.nodes.values()],
        })
        self.record("run_finished", exit_code=exit_code)
        return exit_code
