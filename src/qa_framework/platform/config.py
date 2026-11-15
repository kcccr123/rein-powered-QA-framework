"""Load suite configuration without interpreting game or model settings."""

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

    # Relative output paths belong to the suite file, regardless of the CLI's cwd.
    output_dir = Path(data["output_dir"]).expanduser()
    if not output_dir.is_absolute():
        output_dir = path.resolve().parent / output_dir
    return SuiteConfig(
        image=data["image"],
        episodes_per_trial=data["episodes_per_trial"],
        max_concurrent_trials=data["max_concurrent_trials"],
        output_dir=output_dir.resolve(),
        environments=data["environments"],
        agents=data["agents"],
    )
