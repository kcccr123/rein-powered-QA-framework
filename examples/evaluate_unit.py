"""Evaluate developer-defined unit evidence without starting a game."""

import json

from qa_framework import UnitReport, evaluate_unit


def main() -> None:
    unit = UnitReport(
        unit_id="interaction-check",
        attempt_id="example-attempt-1",
        evidence={
            "interaction_count": 3,
            "activated": True,
            "checkpoints": ["switch", "door"],
        },
    )
    report = evaluate_unit(unit, {
        "target_activated": lambda output: output.evidence["activated"],
        "within_interaction_budget": lambda output: output.evidence["interaction_count"] <= 3,
    })
    print(json.dumps(report.to_dict(), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
