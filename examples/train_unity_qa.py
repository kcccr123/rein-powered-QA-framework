"""Train a gameplay QA PPO/A2C checkpoint through the JSON Unity QaTcpHost.

Start InitialGenerationScene with QaTcpHost listening on port 8800, install
``qa-framework[inference]``, then run this example. Unlike the sibling trainer's
text-protocol UnityGymEnv, this example uses GymnasiumQAEnvironment over the QA
JSON connection, preserving its 119-value observation and five-action codec.

Every reset supplies the complete fixed seven-field dungeon recipe and selected
generation seed. Episode limits apply to every training episode. This is a small
single-layout training entrypoint; evaluate held-out levels before using a model
as evidence about dungeon quality. PPO completes its 256-step rollout, so actual
timesteps can exceed --total-timesteps.

``--fake`` exercises the training plumbing with synthetic observations and rewards.
Its output is a smoke-test artifact, never a trained gameplay model. This example
keeps learning outside the core QA package and changes no Unity or RL repository.
"""

from __future__ import annotations

import argparse
from importlib import import_module
from pathlib import Path
import sys

from qa_framework import EpisodeConfig, FakeQAEnvironment, LevelSpec, UnityQAEnvironment
from qa_framework.schema.validation import validate_finite_number, validate_integer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8800)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--algorithm", choices=("ppo", "a2c"), default="ppo")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--total-timesteps", type=int, default=10_000)
    parser.add_argument("--output", type=Path, default=Path("artifacts/qa/final_model.zip"))
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--max-steps", type=int, default=1000)
    parser.add_argument("--max-duration-seconds", type=float)
    parser.add_argument("--fake", action="store_true", help="Synthetic plumbing smoke test only.")
    return parser


def train(args: argparse.Namespace) -> Path:
    """Train one supplied configuration and close its environment on every exit."""
    validate_integer(args.seed, name="seed")
    if args.seed > 2_147_483_647:
        raise ValueError("seed must fit a nonnegative 32-bit integer")
    validate_integer(args.total_timesteps, name="total_timesteps", minimum=1)
    validate_integer(args.port, name="port", minimum=1)
    if args.port > 65535:
        raise ValueError("port must be at most 65535")
    validate_finite_number(args.timeout_seconds, name="timeout_seconds", minimum=0)
    if args.timeout_seconds == 0:
        raise ValueError("timeout_seconds must be positive")
    if not isinstance(args.host, str) or not args.host.strip():
        raise ValueError("host must be a nonempty string")
    if not isinstance(args.device, str) or not args.device.strip():
        raise ValueError("device must be a nonempty string")
    if args.algorithm not in ("ppo", "a2c"):
        raise ValueError("algorithm must be ppo or a2c")
    output = Path(args.output)
    if output.suffix.lower() != ".zip":
        output = Path(str(output) + ".zip")
    config = EpisodeConfig(
        max_steps=args.max_steps,
        max_duration_seconds=args.max_duration_seconds,
        level=LevelSpec(
            level_id=f"qa-training-{args.seed}",
            generation_seed=args.seed,
            generation_parameters={
                "RoomCount": 10, "AreaWidth": 100, "AreaHeight": 100,
                "MinRoomWidth": 5, "MaxRoomWidth": 10,
                "MinRoomHeight": 5, "MaxRoomHeight": 10,
            },
        ),
    )
    try:
        sb3 = import_module("stable_baselines3")
        wrapper_class = import_module("qa_framework.environments.gymnasium").GymnasiumQAEnvironment
    except ImportError as error:
        raise ImportError(
            "QA training requires the optional dependencies: install qa-framework[inference]."
        ) from error

    if args.fake:
        print("Synthetic smoke test: this checkpoint will not be a trained gameplay model.")
    environment = FakeQAEnvironment() if args.fake else UnityQAEnvironment(
        host=args.host, port=args.port, timeout_seconds=args.timeout_seconds,
    )
    wrapper = None
    try:
        wrapper = wrapper_class(environment, default_config=config)
        algorithm = getattr(sb3, args.algorithm.upper())
        parameters = {"seed": args.seed, "device": args.device, "verbose": 1}
        if args.algorithm == "ppo":
            parameters.update(n_steps=256, batch_size=64)
        else:
            parameters.update(n_steps=5)
        model = algorithm("MlpPolicy", wrapper, **parameters)
        output.parent.mkdir(parents=True, exist_ok=True)
        model.learn(total_timesteps=args.total_timesteps)
        model.save(str(output))
        print(f"Saved {args.algorithm.upper()} QA checkpoint: {output}")
        return output
    finally:
        if wrapper is not None:
            wrapper.close()
        else:
            environment.close()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        train(args)
        return 0
    except KeyboardInterrupt:
        print("QA training interrupted; environment closed.")
        return 130
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as error:
        print(f"QA training failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
