"""Run adaptive gameplay QA rounds against a running Unity QaTcpHost.

Start InitialGenerationScene in the Unity Editor first. By default a local
ray/health heuristic controls the player; --model-path loads a compatible QA
PPO/A2C checkpoint. Each full episode adjusts the next level's geometry.
--episodes 0 runs until Ctrl+C. --fake exercises orchestration without Unity.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import sys

from qa_framework import (
    EpisodeConfig, FakeQAEnvironment, LevelSpec, QAContractError,
    QAEnvironmentError, TerminalReason, UnityQAEnvironment,
)
from qa_framework.orchestration import iter_adaptive_rounds
from qa_framework.policies import RayHeuristicPolicy, StableBaselinesPolicy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8800)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--episodes", type=int, default=1,
                        help="Number of rounds over one connection; 0 runs until Ctrl+C.")
    parser.add_argument("--seed", type=int, default=42, help="Initial level generation seed.")
    parser.add_argument("--qa-seed", type=int, default=0, help="Initial QA evaluation seed.")
    parser.add_argument("--max-steps", type=int, default=1000)
    parser.add_argument("--max-duration-seconds", type=float)
    parser.add_argument("--model-path", type=Path, help="QA gameplay checkpoint .zip, not a PCG model.")
    parser.add_argument("--algorithm", choices=("ppo", "a2c"), default="ppo")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--report-path", type=Path, help="Append one complete round report per JSONL line.")
    parser.add_argument("--run-id", default="live-qa")
    parser.add_argument("--generator-version", help="Recorded game/settings revision for replay.")
    parser.add_argument("--fake", action="store_true", help="Use synthetic gameplay for an offline demo.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.episodes < 0:
        parser.error("--episodes must be nonnegative")
    if not 0 <= args.qa_seed <= 2_147_483_647:
        parser.error("--qa-seed must be a nonnegative 32-bit integer")
    if not args.run_id.strip():
        parser.error("--run-id must not be empty")

    try:
        config = EpisodeConfig(max_steps=args.max_steps, max_duration_seconds=args.max_duration_seconds)
        initial_level = LevelSpec(
            level_id=f"{args.run_id}-level", generation_seed=args.seed,
            generation_parameters={
                "RoomCount": 10, "AreaWidth": 100, "AreaHeight": 100,
                "MinRoomWidth": 5, "MaxRoomWidth": 10,
                "MinRoomHeight": 5, "MaxRoomHeight": 10,
            },
            generator_version=args.generator_version,
        )
        policy = (
            StableBaselinesPolicy.load(args.model_path, algorithm=args.algorithm, device=args.device)
            if args.model_path else RayHeuristicPolicy()
        )
        policy_id = f"{args.algorithm}:{args.model_path.resolve()}" if args.model_path else "ray-heuristic-v1"
        with ExitStack() as resources:
            report_stream = None
            if args.report_path:
                args.report_path.parent.mkdir(parents=True, exist_ok=True)
                report_stream = resources.enter_context(args.report_path.open("a", encoding="utf-8"))
            env = FakeQAEnvironment() if args.fake else UnityQAEnvironment(
                host=args.host, port=args.port, timeout_seconds=args.timeout_seconds,
            )
            resources.callback(env.close)
            print(f"Environment: {type(env).__name__}; policy: {policy_id}")
            for report in iter_adaptive_rounds(
                env, policy, initial_level, rounds=args.episodes or None,
                config=config, qa_seed=args.qa_seed, policy_id=policy_id, run_id=args.run_id,
            ):
                if report_stream is not None:
                    report_stream.write(json.dumps(report.to_dict(), allow_nan=False) + "\n")
                    report_stream.flush()
                evaluation = report.evaluation
                metrics = evaluation.result.metrics
                print(
                    f"[round {report.round_number}] {evaluation.result.terminal_reason.value}; "
                    f"steps={metrics.step_count}; reward={evaluation.total_reward:.2f}; "
                    f"damage={metrics.damage_taken:.2f}; stuck={metrics.stuck_events}; "
                    f"difficulty={report.state_before.difficulty:.3f}->{report.state_after.difficulty:.3f}"
                )
                if report.next_level is not None:
                    parameters = report.next_level.generation_parameters
                    print(
                        f"  next: rooms={parameters['RoomCount']}, "
                        f"area={parameters['AreaWidth']}x{parameters['AreaHeight']}, "
                        f"seed={report.next_level.generation_seed}"
                    )
                if evaluation.result.terminal_reason is TerminalReason.ENVIRONMENT_FAILURE:
                    print(f"Unity session ended: {evaluation.error}", file=sys.stderr)
                    return 1
        return 0
    except KeyboardInterrupt:
        print("Stopped by user.")
        return 0
    except (QAEnvironmentError, QAContractError, OSError, ImportError, TypeError, ValueError) as error:
        print(f"QA run failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
