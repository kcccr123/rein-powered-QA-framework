"""Consume the QA API with a random policy; no learning framework is required."""

from __future__ import annotations

import random

from qa_framework import FakeQAEnvironment, QAAction, QAEnvironment


def choose_random_action(rng: random.Random) -> QAAction:
    """Return a valid action using randomness owned by the example policy."""
    return QAAction(
        move_x=rng.uniform(-1.0, 1.0),
        move_y=rng.uniform(-1.0, 1.0),
        attack=rng.choice((False, True)),
        interact=rng.choice((False, True)),
        ability=rng.randrange(2),
    )


def main() -> None:
    env: QAEnvironment = FakeQAEnvironment()
    policy_rng = random.Random(123)
    total_reward = 0.0
    try:
        observation, info = env.reset(seed=123)
        while True:
            action = choose_random_action(policy_rng)
            observation, reward, terminated, truncated, info = env.step(action)
            total_reward += reward
            if terminated or truncated:
                break
        print(f"Episode ended after {info['step']} steps; reward={total_reward:.1f}")
        print(env.last_metrics)
    finally:
        env.close()


if __name__ == "__main__":
    main()
