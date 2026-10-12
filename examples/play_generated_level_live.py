"""Drive a running Unity QA host instead of synthetic gameplay.

Unlike the other examples, this one is not self-contained: it requires
InitialGenerationScene open and playing in the Unity Editor with a QaTcpHost
component listening on the given port first. See
docs/integration-readiness.md for the wire contract and setup steps.

Runs one episode per connection by default. Pass --episodes to run several
in a row over the same connection (each reset regenerates the dungeon with a
new seed), or --episodes 0 to keep going until interrupted with Ctrl+C.
"""

from __future__ import annotations

import argparse

from qa_framework import EpisodeConfig, LevelSpec, QAAction
from qa_framework.environments.unity_socket import UnityQAEnvironment


def run_episode(env: UnityQAEnvironment, episode_number: int, seed: int) -> None:
    level = LevelSpec(
        level_id=f"live-smoke-test-{episode_number}",
        generation_seed=seed,
        generation_parameters={
            "RoomCount": 10,
            "AreaWidth": 100,
            "AreaHeight": 100,
            "MinRoomWidth": 5,
            "MaxRoomWidth": 10,
            "MinRoomHeight": 5,
            "MaxRoomHeight": 10,
        },
    )

    observation, info = env.reset(seed=seed, config=EpisodeConfig(level=level))
    print(f"[episode {episode_number}] reset acknowledged level_id={info.get('level_id')!r}")

    total_reward = 0.0
    while True:
        # Replace this with a real policy; move_x=0.5 just proves the loop runs.
        action = QAAction(move_x=0.5)
        observation, reward, terminated, truncated, info = env.step(action)
        total_reward += reward
        if terminated or truncated:
            break

    print(f"[episode {episode_number}] ended after {info['step']} steps; reward={total_reward:.1f}")
    print(f"[episode {episode_number}] {env.last_metrics}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8800)
    parser.add_argument(
        "--episodes", type=int, default=1,
        help="Number of episodes to run over one connection; 0 runs until Ctrl+C.",
    )
    parser.add_argument("--seed", type=int, default=42, help="Seed for the first episode.")
    args = parser.parse_args()

    env = UnityQAEnvironment(port=args.port)
    try:
        episode_number = 1
        while args.episodes == 0 or episode_number <= args.episodes:
            run_episode(env, episode_number, seed=args.seed + episode_number - 1)
            episode_number += 1
    except KeyboardInterrupt:
        print("Stopped by user.")
    finally:
        env.close()


if __name__ == "__main__":
    main()
