"""Exercise the future PCG -> QA -> report contract using synthetic gameplay only."""

import json

from qa_framework import (
    EpisodeConfig,
    EvaluationRequest,
    FakeQAEnvironment,
    LevelSpec,
    QAAction,
    evaluate_episode,
)


def main() -> None:
    # These are the current game's parameter names. This example does not call PCG.
    level = LevelSpec(
        level_id="offline-candidate-42",
        generation_seed=42,
        generation_parameters={
            "RoomCount": 5,
            "AreaWidth": 80,
            "AreaHeight": 80,
            "MinRoomWidth": 8,
            "MaxRoomWidth": 12,
            "MinRoomHeight": 8,
            "MaxRoomHeight": 12,
        },
        generator_version="example-game-revision/settings-revision",
    )
    request = EvaluationRequest(
        request_id="offline-evaluation-1",
        level=level,
        qa_seed=123,
        config=EpisodeConfig(max_steps=10),
        policy_id="example-noop-policy",
    )
    environment = FakeQAEnvironment(completion_steps=3)
    try:
        report = evaluate_episode(environment, lambda _: QAAction.noop(), request)
        print(json.dumps(report.to_dict(), indent=2, allow_nan=False))
    finally:
        environment.close()


if __name__ == "__main__":
    main()
