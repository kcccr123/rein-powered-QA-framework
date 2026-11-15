"""Validate managed model and environment assignments before preparing a suite."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from qa_framework.schema.json_data import copy_json_object


@dataclass(frozen=True, slots=True)
class SuiteConfig:
    image: str
    episodes_per_trial: int
    max_concurrent_trials: int
    output_dir: Path
    environments: list[dict[str, object]]
    agents: list[dict[str, object]]
    unity_editor: Path
    preparation_timeout: int = 600


def load_suite(path: Path) -> SuiteConfig:
    """Validate all assignments before contacting Docker or writing output."""
    try:
        import yaml
    except ImportError as error:
        raise ValueError('install YAML support with: pip install "qa-framework[platform]"') from error

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ValueError(f"invalid suite YAML: {error}") from error
    data = copy_json_object(data, name="suite")

    for key in ("image", "output_dir"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"{key} must be a nonempty string")
    for key in ("episodes_per_trial", "max_concurrent_trials"):
        if type(data.get(key)) is not int or data[key] <= 0:
            raise ValueError(f"{key} must be a positive integer")
    for key in ("environments", "agents"):
        definitions = data.get(key)
        if not isinstance(definitions, list) or not definitions:
            raise ValueError(f"{key} must be a nonempty list")
        seen = set()
        for definition in definitions:
            if not isinstance(definition, dict):
                raise ValueError(f"each {key} entry must be an object")
            identity = definition.get("id")
            if not isinstance(identity, str) or not identity.strip():
                raise ValueError(f"each {key} entry needs a nonempty string id")
            if identity in seen:
                raise ValueError(f"duplicate {key} id: {identity}")
            seen.add(identity)

    def host_path(value: str) -> Path:
        resolved = Path(value).expanduser()
        if not resolved.is_absolute():
            resolved = path.resolve().parent / resolved
        return resolved.resolve()

    preparation = data.get("model_preparation")
    if not isinstance(preparation, dict):
        raise ValueError("model_preparation is required")
    editor = preparation.get("unity_editor")
    if not isinstance(editor, str) or not editor.strip() or not host_path(editor).is_file():
        raise ValueError("model_preparation.unity_editor must name an existing Unity Editor executable")
    timeout = preparation.get("timeout_seconds", 600)
    if type(timeout) is not int or timeout <= 0:
        raise ValueError("model_preparation.timeout_seconds must be a positive integer")
    for environment in data["environments"]:
        scene = environment.get("scene")
        if not isinstance(scene, str) or not scene.strip():
            raise ValueError("each environment.scene must name a scene in the Player build")
    for agent in data["agents"]:
        model = agent.get("model")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("each agent.model must name a self-contained ONNX file")
        model_path = host_path(model)
        if model_path.suffix.lower() != ".onnx" or not model_path.is_file():
            raise ValueError(f"agent.model must be an existing ONNX file: {model}")
        agent["model"] = str(model_path)

    output_dir = host_path(data["output_dir"])
    return SuiteConfig(
        image=data["image"],
        episodes_per_trial=data["episodes_per_trial"],
        max_concurrent_trials=data["max_concurrent_trials"],
        output_dir=output_dir.resolve(),
        environments=data["environments"],
        agents=data["agents"],
        unity_editor=host_path(editor),
        preparation_timeout=timeout,
    )
