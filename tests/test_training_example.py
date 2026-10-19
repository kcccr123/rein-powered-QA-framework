"""Verify QA training wiring without installing SB3 or running optimization."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest

from qa_framework import FakeQAEnvironment, QAAction, TerminalReason


@pytest.fixture
def example():
    path = Path(__file__).resolve().parents[1] / "examples" / "train_unity_qa.py"
    spec = spec_from_file_location("qa_training_example", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingFake(FakeQAEnvironment):
    def __init__(self):
        super().__init__()
        self.reset_calls = []
        self.actions = []
        self.close_calls = 0

    def reset(self, *, seed=None, config=None):
        self.reset_calls.append((seed, config))
        return super().reset(seed=seed, config=config)

    def step(self, action):
        self.actions.append(action)
        return super().step(action)

    def close(self):
        self.close_calls += 1
        super().close()


@pytest.fixture
def stub_trainer(example, monkeypatch):
    np = pytest.importorskip("numpy")
    pytest.importorskip("gymnasium")
    original_import = example.import_module
    state = SimpleNamespace(models=[], fail_learn=False, fail_construct=False)

    class Algorithm:
        def __init__(self, policy_name, env, **parameters):
            if state.fail_construct:
                raise RuntimeError("model initialization failed")
            self.policy_name = policy_name
            self.env = env
            self.parameters = parameters
            self.outcomes = []
            self.saved = None
            state.models.append(self)

        def learn(self, *, total_timesteps):
            self.total_timesteps = total_timesteps
            if state.fail_learn:
                raise RuntimeError("training failed")
            # Exercise real numerical reset/step plumbing, without learning.
            for seed in (self.parameters["seed"], None):
                values, _info = self.env.reset(seed=seed)
                assert values.shape == (119,)
                assert values.dtype == np.float32
                while True:
                    values, reward, terminated, truncated, info = self.env.step(
                        np.asarray([0.25, -0.5, 1, -1, -1], dtype=np.float32)
                    )
                    if terminated or truncated:
                        self.outcomes.append(info["episode_result"])
                        break
            return self

        def save(self, path):
            self.saved = path
            Path(path).write_bytes(b"mock checkpoint; no optimization performed")

    monkeypatch.setattr(
        example, "import_module",
        lambda name: SimpleNamespace(PPO=Algorithm, A2C=Algorithm)
        if name == "stable_baselines3" else original_import(name),
    )
    return state


@pytest.mark.parametrize("algorithm", ["ppo", "a2c"])
def test_training_uses_qa_numeric_contract_and_full_level_on_every_reset(
    example, stub_trainer, monkeypatch, tmp_path, algorithm, capsys
):
    backend = RecordingFake()
    monkeypatch.setattr(example, "FakeQAEnvironment", lambda: backend)
    output = tmp_path / "models" / "gameplay.zip"
    result = example.main([
        "--fake", "--algorithm", algorithm, "--seed", "123", "--max-steps", "2",
        "--max-duration-seconds", "3", "--total-timesteps", "8", "--output", str(output),
    ])
    assert result == 0
    assert backend.close_calls == 1
    assert len(backend.reset_calls) == 2
    assert [seed for seed, _ in backend.reset_calls] == [123, None]
    for _seed, config in backend.reset_calls:
        assert config.max_steps == 2
        assert config.max_duration_seconds == 3
        assert config.level.generation_seed == 123
        assert dict(config.level.generation_parameters) == {
            "RoomCount": 10, "AreaWidth": 100, "AreaHeight": 100,
            "MinRoomWidth": 5, "MaxRoomWidth": 10,
            "MinRoomHeight": 5, "MaxRoomHeight": 10,
        }
    assert backend.actions == [QAAction(0.25, -0.5, True)] * 4
    model = stub_trainer.models[0]
    assert model.policy_name == "MlpPolicy"
    assert model.parameters["seed"] == 123
    assert model.parameters["device"] == "cpu"
    assert model.total_timesteps == 8
    assert [result.terminal_reason for result in model.outcomes] == [TerminalReason.MAX_STEPS] * 2
    assert model.saved == str(output)
    assert output.is_file()
    if algorithm == "ppo":
        assert model.parameters["n_steps"] == 256
        assert model.parameters["batch_size"] == 64
    else:
        assert model.parameters["n_steps"] == 5
        assert "batch_size" not in model.parameters
    assert "will not be a trained gameplay model" in capsys.readouterr().out


def test_unity_training_passes_connection_settings_and_closes_after_learning(
    example, stub_trainer, monkeypatch, tmp_path
):
    backend = RecordingFake()
    connections = []

    def connect(**parameters):
        connections.append(parameters)
        return backend

    monkeypatch.setattr(example, "UnityQAEnvironment", connect)
    output = tmp_path / "gameplay"
    assert example.main([
        "--host", "localhost", "--port", "8801", "--timeout-seconds", "4",
        "--total-timesteps", "8", "--max-steps", "1", "--output", str(output),
    ]) == 0
    assert connections == [{"host": "localhost", "port": 8801, "timeout_seconds": 4}]
    assert backend.close_calls == 1
    assert stub_trainer.models[0].saved == str(output) + ".zip"


@pytest.mark.parametrize("stage", ["fail_learn", "fail_construct"])
def test_model_failure_still_closes_backend(example, stub_trainer, monkeypatch, tmp_path, stage):
    backend = RecordingFake()
    monkeypatch.setattr(example, "FakeQAEnvironment", lambda: backend)
    setattr(stub_trainer, stage, True)
    output = tmp_path / "failed.zip"
    assert example.main(["--fake", "--output", str(output)]) == 1
    assert backend.close_calls == 1
    assert not output.exists()


def test_wrapper_failure_still_closes_backend(example, monkeypatch, tmp_path):
    backend = RecordingFake()
    monkeypatch.setattr(example, "FakeQAEnvironment", lambda: backend)

    def broken_wrapper(*_args, **_kwargs):
        raise RuntimeError("wrapper setup failed")

    monkeypatch.setattr(
        example, "import_module",
        lambda name: SimpleNamespace() if name == "stable_baselines3"
        else SimpleNamespace(GymnasiumQAEnvironment=broken_wrapper),
    )
    assert example.main(["--fake", "--output", str(tmp_path / "failed.zip")]) == 1
    assert backend.close_calls == 1


def test_missing_optional_dependencies_fail_before_unity_connection(example, monkeypatch, capsys):
    def forbidden_connection(**_kwargs):
        pytest.fail("attempted to connect before verifying training dependencies")

    def missing(_name):
        raise ImportError("not installed")

    monkeypatch.setattr(example, "UnityQAEnvironment", forbidden_connection)
    monkeypatch.setattr(example, "import_module", missing)
    assert example.main([]) == 1
    assert "qa-framework[inference]" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [
        ["--total-timesteps", "0"], ["--seed", "-1"], ["--seed", "2147483648"],
        ["--max-steps", "0"], ["--max-duration-seconds", "-1"],
        ["--port", "70000"], ["--timeout-seconds", "nan"], ["--timeout-seconds", "0"],
    ],
)
def test_invalid_training_limits_fail_before_loading_or_connecting(example, monkeypatch, arguments):
    def forbidden(_name):
        pytest.fail("invalid settings reached optional imports")

    monkeypatch.setattr(example, "import_module", forbidden)
    assert example.main(arguments) == 1


def test_help_needs_no_optional_training_dependencies(example, monkeypatch, capsys):
    def forbidden(_name):
        pytest.fail("--help imported an optional training dependency")

    monkeypatch.setattr(example, "import_module", forbidden)
    with pytest.raises(SystemExit) as error:
        example.main(["--help"])
    assert error.value.code == 0
    assert "--total-timesteps" in capsys.readouterr().out
