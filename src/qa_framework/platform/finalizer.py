"""Collect QA results and run developer analysis in a finalizer image."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import runpy
import sys
import traceback
from typing import TextIO

from qa_framework import UnitReport
from qa_framework.schema.json_data import copy_json_object


def read_object(path: Path) -> dict:
    return copy_json_object(json.loads(path.read_text(encoding="utf-8")), name=str(path))


def read_records(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, start=1):
            try:
                yield copy_json_object(json.loads(line), name="record")
            except (TypeError, ValueError) as error:
                raise ValueError(f"{path}:{number}: {error}") from error


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_record(stream: TextIO, value: dict) -> None:
    stream.write(json.dumps(value, allow_nan=False) + "\n")


def training_records(source: Path, manifest: dict, runs: TextIO, metrics: TextIO) -> dict:
    output = source / "output"
    node_id = manifest["node_id"]
    artifact_path = output / "artifacts.json"
    artifacts = read_object(artifact_path) if artifact_path.is_file() else None
    path = output / "progress.json"
    saved = 0
    error = None
    if path.is_file():
        try:
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    # PPO can emit NaN for undefined statistics such as explained variance.
                    snapshot = copy_json_object(json.loads(line, parse_constant=lambda _: None), name="training metrics")
                    write_record(metrics, {"node_id": node_id, "metrics": snapshot})
                    saved += 1
        except (OSError, TypeError, ValueError) as failure:
            error = f"{path.name}, row {saved + 1}: {failure}"
    if manifest["status"] == "succeeded" and artifacts is None:
        error = "missing training artifact manifest"
    if artifacts is not None and artifacts.get("metrics") and not path.is_file():
        error = "missing published training metrics"
    write_record(runs, {"node_id": node_id, "status": manifest["status"], "config": manifest["config"],
                        "artifacts": artifacts, "metrics_available": path.is_file()})
    return {"metric_records": saved, "metrics_available": path.is_file(), "data_error": error}


def trial_records(path: Path, assignment: dict, output: TextIO) -> tuple[int, str | None]:
    count = 0
    try:
        for row in read_records(path):
            for key, expected in (("suite_id", assignment["suite_id"]), ("trial_id", assignment["trial_id"]),
                                  ("agent_id", assignment["agent"]["id"]),
                                  ("environment_id", assignment["environment"]["id"]), ("episode_index", count + 1)):
                if row.get(key) != expected:
                    raise ValueError(f"episode {count + 1} has an unexpected {key}")
            UnitReport.from_dict(row["report"])
            if count >= assignment["episodes_per_trial"]:
                raise ValueError("trial contains more episodes than assigned")
            write_record(output, row)
            count += 1
    except (OSError, KeyError, TypeError, ValueError) as error:
        return count, str(error)
    return count, None


def suite_records(source: Path, manifest: dict, output: TextIO) -> dict:
    jobs_path = source / "jobs.jsonl"
    expected_episodes = manifest["total_jobs"] * manifest["config"]["episodes_per_trial"]
    trials = []
    received = 0
    if jobs_path.is_file():
        for job in read_records(jobs_path):
            assignment = job["assignment"]
            path = source / job["directory"] / "output" / "episodes.jsonl"
            expected = assignment["episodes_per_trial"]
            count, error = trial_records(path, assignment, output)
            if error is None and count != expected:
                error = f"expected {expected} episodes, received {count}"
            trials.append({"trial_id": assignment["trial_id"], "agent_id": assignment["agent"]["id"],
                           "environment_id": assignment["environment"]["id"], "exit_code": job["exit_code"],
                           "job_error": job["error"], "data_error": error,
                           "expected_episodes": expected, "received_episodes": count})
            received += count
    error = None
    if len(trials) != manifest["total_jobs"] or received != expected_episodes or any(
        trial["data_error"] is not None or trial["job_error"] is not None for trial in trials
    ):
        error = "trial results are incomplete; see trial counts and errors"
    return {"expected_trials": manifest["total_jobs"], "recorded_trials": len(trials),
            "expected_episodes": expected_episodes, "received_episodes": received, "trials": trials, "data_error": error}


def collect_dataset(directory: Path, groups: dict[str, list[Path]]) -> dict:
    directory.mkdir()
    summaries = {}
    with ExitStack() as stack:
        if "training" in groups:
            runs = stack.enter_context((directory / "training.jsonl").open("x", encoding="utf-8"))
            metrics = stack.enter_context((directory / "training_metrics.jsonl").open("x", encoding="utf-8"))
        if "testing" in groups:
            episodes = stack.enter_context((directory / "episodes.jsonl").open("x", encoding="utf-8"))
        for kind, sources in groups.items():
            summaries[kind] = []
            for source in sources:
                manifest = read_object(source / "manifest.json")
                details = training_records(source, manifest, runs, metrics) if kind == "training" else suite_records(source, manifest, episodes)
                summaries[kind].append({"node_id": manifest["node_id"], "status": manifest["status"],
                                        "job_error": manifest["error"], **details})
    complete = all(source["status"] == "succeeded" and source["data_error"] is None
                   for sources in summaries.values() for source in sources)
    index = {"schema_version": 1, "complete": complete, **summaries}
    write_json(directory / "index.json", index)
    return index


@dataclass(frozen=True, slots=True)
class AnalysisContext:
    """The selected dataset and filesystem locations for an analysis script."""

    kind: str
    index: dict
    dataset_dir: Path
    upstream_dir: Path
    output_dir: Path

    def records(self) -> Iterator[dict]:
        name = "training.jsonl" if self.kind == "training" else "episodes.jsonl"
        return read_records(self.dataset_dir / name)

    def training_metrics(self) -> Iterator[dict]:
        if self.kind != "training":
            raise ValueError("training_metrics() requires a training script")
        return read_records(self.dataset_dir / "training_metrics.jsonl")


def skip_analysis(output_dir: Path, scripts: dict, reason: str) -> None:
    output_dir.mkdir(exist_ok=True)
    entries = [{"kind": kind, "script": path, "status": "skipped", "reason": reason}
               for kind, paths in scripts.items() for path in paths]
    write_json(output_dir / "report.json", {"status": "skipped", "inputs_complete": False,
                                            "reason": reason, "scripts": entries})


def run_analysis(scripts: dict, dataset_dir: Path, upstream_dir: Path, output_dir: Path) -> bool:
    index = read_object(dataset_dir / "index.json")
    if not index["complete"]:
        skip_analysis(output_dir, scripts, "selected results are incomplete; see dataset/index.json")
        return False
    entries = []
    for kind, paths in scripts.items():
        for number, path in enumerate(paths, start=1):
            subdirectory = output_dir / "scripts" / f"{kind}-{number:03d}"
            subdirectory.mkdir(parents=True)
            entry = {"kind": kind, "script": path, "output_dir": subdirectory.relative_to(output_dir).as_posix()}
            try:
                analyze = runpy.run_path(path).get("analyze")
                if not callable(analyze):
                    raise ValueError("script must define analyze(context)")
                context = AnalysisContext(kind, deepcopy(index), dataset_dir, upstream_dir, subdirectory)
                result = copy_json_object(analyze(context), name="analysis result")
                if set(result) - {"metrics", "checks"}:
                    raise ValueError("analysis result accepts only metrics and checks")
                metrics = copy_json_object(result.get("metrics", {}), name="analysis metrics")
                checks = copy_json_object(result.get("checks", {}), name="analysis checks")
                if any(type(passed) is not bool for passed in checks.values()):
                    raise ValueError("analysis checks must return booleans")
                entry.update(status="succeeded", metrics=metrics, checks=checks)
            except (Exception, SystemExit) as error:
                traceback.print_exc()
                entry.update(status="failed", error=f"{type(error).__name__}: {error}")
            entries.append(entry)
    succeeded = all(entry["status"] == "succeeded" for entry in entries)
    write_json(output_dir / "report.json", {"status": "succeeded" if succeeded else "failed",
                                            "inputs_complete": index["complete"], "scripts": entries})
    return succeeded


def main() -> int:
    try:
        assignment = read_object(Path(os.environ["QA_FINALIZER_CONFIG"]))
        return int(not run_analysis(assignment["scripts"], Path("/qa/dataset"), Path("/qa/upstream"),
                                    Path(os.environ["QA_OUTPUT_DIR"])))
    except (OSError, KeyError, TypeError, ValueError) as error:
        print(f"Finalizer failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
