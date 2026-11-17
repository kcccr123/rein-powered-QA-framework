"""Validate a training/evaluation DAG before writing output or contacting Docker."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re

from qa_framework.schema.json_data import copy_json_object


@dataclass(frozen=True, slots=True)
class NodeConfig:
    id: str
    type: str
    image: str
    settings: dict
    dependencies: frozenset[str]


@dataclass(frozen=True, slots=True)
class DAGConfig:
    nodes: list[NodeConfig]
    max_concurrent_containers: int
    output_dir: Path
    unity_editor: Path | None
    preparation_timeout: int = 600


def positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def trainer_settings(value: object, *, resuming: bool) -> dict:
    settings = copy_json_object(value, name="trainer")
    allowed = {"model", "device", "seed", "total_timesteps", "checkpoint_freq",
               "socket_timeout_seconds", "learning_rate", "gamma", "n_steps", "ppo", "ent_coef"}
    unknown = settings.keys() - allowed
    if unknown:
        raise ValueError(f"unknown or platform-owned trainer settings: {', '.join(sorted(unknown))}")
    for key, default in {"model": "ppo", "device": "cpu", "seed": 0, "checkpoint_freq": 0,
                         "socket_timeout_seconds": 300}.items():
        settings.setdefault(key, default)
    if settings["model"] != "ppo" or settings["device"] != "cpu":
        raise ValueError("training currently requires model: ppo and device: cpu")
    positive_int(settings.get("total_timesteps"), "trainer.total_timesteps")
    if type(settings["seed"]) is not int or not 0 <= settings["seed"] <= 2_147_483_647:
        raise ValueError("trainer.seed must be a nonnegative 32-bit integer")
    if type(settings["checkpoint_freq"]) is not int or settings["checkpoint_freq"] < 0:
        raise ValueError("trainer.checkpoint_freq must be a nonnegative integer")

    # Omitted optimizer/rollout settings inherit the checkpoint, never fresh defaults.
    if not resuming:
        for key, default in {"learning_rate": 0.0003, "gamma": 0.99, "n_steps": 256,
                             "ppo": {"batch_size": 64}, "ent_coef": 0.0}.items():
            settings.setdefault(key, default)
    for key in ("learning_rate", "gamma", "ent_coef", "socket_timeout_seconds"):
        if key not in settings:
            continue
        number = settings[key]
        if type(number) not in (int, float) or not math.isfinite(number):
            raise ValueError(f"trainer.{key} must be finite")
        if key == "ent_coef":
            if number < 0:
                raise ValueError("trainer.ent_coef must be nonnegative")
        elif number <= 0 or (key == "gamma" and number > 1):
            raise ValueError(f"invalid trainer.{key}")
    if "n_steps" in settings:
        positive_int(settings["n_steps"], "trainer.n_steps")
        if settings["n_steps"] < 2:
            raise ValueError("trainer.n_steps must be at least 2 for PPO")
    if "ppo" in settings:
        ppo = settings["ppo"]
        if not isinstance(ppo, dict) or set(ppo) != {"batch_size"}:
            raise ValueError("trainer.ppo accepts only batch_size")
        batch = positive_int(ppo["batch_size"], "trainer.ppo.batch_size")
        if batch < 2 or ("n_steps" in settings and batch > settings["n_steps"]):
            raise ValueError("trainer.ppo.batch_size must be between 2 and n_steps")
    return settings


def load_dag(path: Path) -> DAGConfig:
    try:
        import yaml
    except ImportError as error:
        raise ValueError('install YAML support with: pip install "qa-framework[platform]"') from error
    try:
        data = copy_json_object(yaml.safe_load(path.read_text(encoding="utf-8")), name="DAG")
    except yaml.YAMLError as error:
        raise ValueError(f"invalid DAG YAML: {error}") from error
    if set(data) - {"nodes", "max_concurrent_containers", "output_dir", "model_preparation"}:
        raise ValueError("expected DAG fields: nodes, max_concurrent_containers, output_dir, model_preparation; migrate flat suite YAML")

    def text(value: object, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a nonempty string")
        return value

    def host_path(value: str) -> Path:
        resolved = Path(value).expanduser()
        if not resolved.is_absolute():
            resolved = path.resolve().parent / resolved
        return resolved.resolve()

    limit = positive_int(data.get("max_concurrent_containers"), "max_concurrent_containers")
    output_dir = host_path(text(data.get("output_dir"), "output_dir"))
    definitions = data.get("nodes")
    if not isinstance(definitions, list) or not definitions:
        raise ValueError("nodes must be a nonempty list")
    nodes = []
    identities = set()
    for definition in definitions:
        node = copy_json_object(definition, name="node")
        identity = text(node.get("id"), "node.id")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,62}", identity):
            raise ValueError("node.id must start with a lowercase letter and contain at most 63 lowercase letters, digits, _ or -")
        if identity in identities:
            raise ValueError(f"duplicate node id: {identity}")
        identities.add(identity)
        kind = node.get("type")
        if kind not in ("train", "suite"):
            raise ValueError(f"{identity}: node.type must be train or suite")
        image = text(node.get("image"), f"{identity}.image")
        dependencies = set()

        def model_input(value: object, output: str) -> str | dict:
            if isinstance(value, str):
                resolved = host_path(value)
                suffix = ".zip"
                if output == "onnx":
                    suffix = ".onnx"
                if resolved.suffix.lower() != suffix or not resolved.is_file():
                    raise ValueError(f"{identity}: expected an existing {suffix} file: {value}")
                return str(resolved)
            if not isinstance(value, dict) or set(value) != {"node", "output"}:
                raise ValueError(f"{identity}: model input must be a file or {{node: producer, output: {output}}}")
            producer = text(value["node"], "producer node")
            if value["output"] != output:
                raise ValueError(f"{identity}: this input requires output: {output}")
            dependencies.add(producer)
            return value

        def environment(value: object) -> dict:
            result = copy_json_object(value, name="environment")
            text(result.get("id"), "environment.id")
            text(result.get("scene"), "environment.scene")
            result["parameters"] = copy_json_object(result.get("parameters", {}), name="environment.parameters")
            return result

        if kind == "train":
            if set(node) - {"id", "type", "image", "environment", "trainer", "checkpoint"}:
                raise ValueError(f"{identity}: unknown training node fields")
            node["environment"] = environment(node.get("environment"))
            if node.get("checkpoint") is not None:
                node["checkpoint"] = model_input(node["checkpoint"], "checkpoint")
            node["trainer"] = trainer_settings(node.get("trainer"), resuming=node.get("checkpoint") is not None)
        else:
            if set(node) - {"id", "type", "image", "environments", "agents", "episodes_per_trial"}:
                raise ValueError(f"{identity}: unknown suite node fields")
            positive_int(node.get("episodes_per_trial"), "episodes_per_trial")
            for key in ("environments", "agents"):
                entries = node.get(key)
                if not isinstance(entries, list) or not entries:
                    raise ValueError(f"{identity}.{key} must be a nonempty list")
                seen = set()
                normalized = []
                for entry in entries:
                    entry = copy_json_object(entry, name=key)
                    entry_id = text(entry.get("id"), f"{key}.id")
                    if entry_id in seen:
                        raise ValueError(f"{identity}: duplicate {key} id: {entry_id}")
                    seen.add(entry_id)
                    if key == "environments":
                        entry = environment(entry)
                    else:
                        entry["model"] = model_input(entry.get("model"), "onnx")
                    normalized.append(entry)
                node[key] = normalized
        nodes.append(NodeConfig(identity, kind, image, node, frozenset(dependencies)))

    # Validate all components, including cycles disconnected from runnable roots.
    by_id = {node.id: node for node in nodes}
    for node in nodes:
        for dependency in node.dependencies:
            if dependency not in by_id or by_id[dependency].type != "train":
                raise ValueError(f"{node.id}: producer {dependency} must name a training node")
    remaining = {node.id: set(node.dependencies) for node in nodes}
    while remaining:
        ready = {identity for identity, parents in remaining.items() if not parents}
        if not ready:
            raise ValueError(f"DAG contains a cycle: {', '.join(remaining)}")
        remaining = {identity: parents - ready for identity, parents in remaining.items() if identity not in ready}

    preparation = data.get("model_preparation", {})
    if not isinstance(preparation, dict) or set(preparation) - {"unity_editor", "timeout_seconds"}:
        raise ValueError("invalid model_preparation settings")
    editor = None
    if any(node.type == "suite" for node in nodes):
        editor = host_path(text(preparation.get("unity_editor"), "model_preparation.unity_editor"))
        if not editor.is_file():
            raise ValueError("model_preparation.unity_editor must name an existing executable")
    timeout = positive_int(preparation.get("timeout_seconds", 600), "model_preparation.timeout_seconds")
    return DAGConfig(nodes, limit, output_dir, editor, timeout)