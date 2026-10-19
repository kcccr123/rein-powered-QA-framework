"""Verify the live command connects complete rounds and persists their feedback."""

import importlib.util
import json
from pathlib import Path

import pytest

from qa_framework import FakeQAEnvironment, QAEnvironmentError, TerminalReason
from qa_framework.orchestration import RoundReport


@pytest.fixture
def live_example():
    path = Path(__file__).resolve().parents[1] / "examples" / "play_generated_level_live.py"
    spec = importlib.util.spec_from_file_location("qa_live_example_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_reports(path):
    return [RoundReport.from_dict(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()]


def test_fake_command_writes_linked_rounds_and_applies_changed_recipes(
    live_example, monkeypatch, tmp_path,
):
    class RecordingEnvironment(FakeQAEnvironment):
        def __init__(self):
            super().__init__()
            self.loaded_levels = []
            self.closed = False

        def reset(self, *, seed=None, config=None):
            self.loaded_levels.append(config.level)
            return super().reset(seed=seed, config=config)

        def close(self):
            self.closed = True
            super().close()

    environment = RecordingEnvironment()
    monkeypatch.setattr(live_example, "FakeQAEnvironment", lambda: environment)

    def unexpected_connection(**kwargs):
        pytest.fail("--fake must not connect to Unity")

    monkeypatch.setattr(live_example, "UnityQAEnvironment", unexpected_connection)
    report_path = tmp_path / "reports" / "rounds.jsonl"

    assert live_example.main([
        "--fake", "--episodes", "3", "--report-path", str(report_path),
        "--run-id", "integration", "--generator-version", "fixture-game-v1",
    ]) == 0

    reports = read_reports(report_path)
    assert len(reports) == 3
    assert [report.round_number for report in reports] == [1, 2, 3]
    assert [report.evaluation.request.level for report in reports] == environment.loaded_levels
    assert [report.evaluation.request.qa_seed for report in reports] == [0, 1, 2]
    assert [level.generation_seed for level in environment.loaded_levels] == [42, 43, 44]
    assert all(report.evaluation.result.terminal_reason is TerminalReason.COMPLETED for report in reports)
    for previous, current in zip(reports, reports[1:]):
        assert previous.next_level == current.evaluation.request.level
        assert previous.state_after == current.state_before
        before = previous.evaluation.request.level.generation_parameters
        after = current.evaluation.request.level.generation_parameters
        assert any(before[name] != after[name] for name in ("RoomCount", "AreaWidth", "AreaHeight"))
    assert environment.closed


def test_failed_live_round_is_written_before_command_returns_failure(
    live_example, monkeypatch, tmp_path, capsys,
):
    class FailingEnvironment(FakeQAEnvironment):
        closed = False

        def step(self, action):
            if self.last_metrics.step_count == 1:
                raise QAEnvironmentError("fixture connection lost")
            return super().step(action)

        def close(self):
            self.closed = True
            super().close()

    environment = FailingEnvironment()
    monkeypatch.setattr(live_example, "UnityQAEnvironment", lambda **kwargs: environment)
    report_path = tmp_path / "failed.jsonl"

    assert live_example.main(["--episodes", "3", "--report-path", str(report_path)]) == 1

    reports = read_reports(report_path)
    assert len(reports) == 1
    report = reports[0]
    assert report.evaluation.result.terminal_reason is TerminalReason.ENVIRONMENT_FAILURE
    assert report.evaluation.result.metrics.step_count == 1
    assert report.next_level is None and report.skill_signal is None
    assert report.state_after.difficulty == report.state_before.difficulty
    assert report.state_after.skill == report.state_before.skill
    assert "fixture connection lost" in report.evaluation.error
    assert "fixture connection lost" in capsys.readouterr().err
    assert environment.closed


@pytest.mark.parametrize(("argv", "parser_error"), [
    (["--episodes", "-1"], True),
    (["--qa-seed", "-1"], True),
    (["--run-id", " "], True),
    (["--max-steps", "0"], False),
    (["--max-duration-seconds", "-1"], False),
    (["--seed", "-1"], False),
])
def test_invalid_run_arguments_fail_before_connecting(live_example, monkeypatch, argv, parser_error):
    def unexpected_connection(**kwargs):
        pytest.fail("invalid run arguments must fail before connecting to Unity")

    monkeypatch.setattr(live_example, "UnityQAEnvironment", unexpected_connection)
    if parser_error:
        with pytest.raises(SystemExit) as error:
            live_example.main(argv)
        assert error.value.code == 2
    else:
        assert live_example.main(argv) == 1
