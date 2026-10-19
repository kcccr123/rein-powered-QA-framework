"""Gameplay decisions and the optional RL observation/action handoff."""

from types import SimpleNamespace

import pytest

from qa_framework import QAAction, QAObjectType, QAObservation, RayObservation
from qa_framework.policies import RayHeuristicPolicy, StableBaselinesPolicy
from qa_framework.schema import flatten_observation, get_flat_observation_size


def observation(*, rays=None, health=1.0, previous_action=None, powerup_count=0):
    if rays is None:
        rays = [RayObservation(False, 1.0, QAObjectType.NONE) for _ in range(16)]
    return QAObservation(
        health_01=health,
        powerup_count=powerup_count,
        rays=tuple(rays),
        previous_action=previous_action or QAAction.noop(),
        ray_count=len(rays),
    )


def with_targets(*targets, health=1.0):
    rays = list(observation().rays)
    for index, category, distance in targets:
        rays[index] = RayObservation(True, distance, category)
    return observation(rays=rays, health=health)


@pytest.mark.parametrize(
    "index, expected",
    [(0, (0, 1)), (4, (1, 0)), (8, (0, -1)), (12, (-1, 0))],
)
def test_heuristic_uses_unity_world_ray_directions(index, expected):
    sensed = with_targets((index, QAObjectType.EXIT, 0.6))
    action = RayHeuristicPolicy()(sensed)
    assert (action.move_x, action.move_y) == pytest.approx(expected)


def test_heuristic_changes_heading_when_wall_blocks_previous_movement():
    rays = list(observation().rays)
    rays[4] = RayObservation(True, 0.01, QAObjectType.OBSTACLE)
    sensed = observation(rays=rays, previous_action=QAAction(move_x=1))
    action = RayHeuristicPolicy()(sensed)
    assert action.move_x < 1
    assert abs(action.move_y) > 0


def test_heuristic_prioritizes_exit_then_loot_and_uses_buttons_only_near_targets():
    policy = RayHeuristicPolicy()
    sensed = with_targets(
        (4, QAObjectType.EXIT, 0.8),
        (8, QAObjectType.LOOT, 0.05),
        (12, QAObjectType.ENEMY, 0.05),
    )
    action = policy(sensed)
    assert action == QAAction(move_x=1, attack=True, interact=True)
    distant = with_targets((8, QAObjectType.LOOT, 0.5), (12, QAObjectType.ENEMY, 0.5))
    assert policy(distant) == QAAction(move_y=-1)


def test_low_health_retreats_from_enemy_while_healthy_agent_approaches():
    policy = RayHeuristicPolicy()
    healthy = with_targets((4, QAObjectType.ENEMY, 0.05))
    injured = with_targets((4, QAObjectType.ENEMY, 0.05), health=0.1)
    assert policy(healthy) == QAAction(move_x=1, attack=True)
    assert policy(injured) == QAAction(move_x=-1, attack=True)
    assert policy(with_targets((4, QAObjectType.ENEMY, 0.05), health=0)) == QAAction.noop()


@pytest.mark.parametrize("category", [QAObjectType.OBSTACLE, QAObjectType.NONE])
def test_heuristic_stops_when_boxed_in_including_unclassified_hits(category):
    sensed = observation(rays=[RayObservation(True, 0.01, category)] * 16)
    assert RayHeuristicPolicy()(sensed) == QAAction.noop()


def test_heuristic_keeps_heading_in_clear_space_and_supports_explicit_ray_count():
    sensed = observation(previous_action=QAAction(move_x=-1))
    assert RayHeuristicPolicy()(sensed) == QAAction(move_x=-1)
    four_rays = [RayObservation(False, 1, QAObjectType.NONE)] * 4
    four_rays[1] = RayObservation(True, 0.3, QAObjectType.EXIT)
    assert RayHeuristicPolicy(ray_count=4)(observation(rays=four_rays)) == QAAction(move_x=1)
    with pytest.raises(ValueError, match="16 rays"):
        RayHeuristicPolicy()(observation(rays=four_rays))


@pytest.mark.parametrize(
    "parameters", [{"ray_count": 0}, {"action_distance_01": 1.5}, {"low_health_threshold": -1}]
)
def test_heuristic_rejects_invalid_sensor_configuration(parameters):
    with pytest.raises(ValueError):
        RayHeuristicPolicy(**parameters)


@pytest.fixture
def numeric_model():
    np = pytest.importorskip("numpy")
    spaces = pytest.importorskip("gymnasium.spaces")

    class Model:
        observation_space = spaces.Box(
            low=-np.inf, high=np.inf,
            shape=(get_flat_observation_size(),), dtype=np.float32,
        )
        action_space = spaces.Box(-1, 1, shape=(5,), dtype=np.float32)

        def __init__(self):
            self.action = np.array([0.25, -0.5, 0.3, -0.4, 0.5], dtype=np.float32)
            self.calls = []

        def predict(self, values, *, deterministic):
            self.calls.append((values.copy(), deterministic))
            return self.action, None

    return Model(), np, spaces


def test_checkpoint_policy_flattens_latest_rays_health_counts_and_previous_action(numeric_model):
    model, np, _ = numeric_model
    policy = StableBaselinesPolicy(model, max_ability_slot=4)
    sensed = with_targets((4, QAObjectType.ENEMY, 0.1), health=0.2)
    sensed = observation(
        rays=sensed.rays, health=sensed.health_01,
        previous_action=QAAction(-0.3, 0.6, True, False, 3), powerup_count=7,
    )
    assert policy(sensed) == QAAction(0.25, -0.5, True, False, 3)
    values, deterministic = model.calls[0]
    assert deterministic is True
    assert values.dtype == np.float32
    np.testing.assert_allclose(values, flatten_observation(sensed))
    changed = observation(rays=observation().rays, health=0.9)
    policy(changed)
    np.testing.assert_allclose(model.calls[1][0], flatten_observation(changed))
    assert not np.array_equal(model.calls[0][0], model.calls[1][0])


def test_checkpoint_policy_can_request_stochastic_inference(numeric_model):
    model, _, _ = numeric_model
    StableBaselinesPolicy(model, deterministic=False)(observation())
    assert model.calls[0][1] is False


@pytest.mark.parametrize("shape", [(3,), (1, 119), (118,)])
def test_checkpoint_policy_rejects_incompatible_observation_spaces(numeric_model, shape):
    model, np, spaces = numeric_model
    model.observation_space = spaces.Box(-np.inf, np.inf, shape=shape, dtype=np.float32)
    with pytest.raises(ValueError, match="PCG checkpoints are incompatible"):
        StableBaselinesPolicy(model)


@pytest.mark.parametrize("shape, low, high", [((3,), -1, 1), ((5,), 0, 1), ((5,), -2, 2)])
def test_checkpoint_policy_rejects_incompatible_action_contracts(numeric_model, shape, low, high):
    model, np, spaces = numeric_model
    model.action_space = spaces.Box(low, high, shape=shape, dtype=np.float32)
    with pytest.raises(ValueError, match="action space"):
        StableBaselinesPolicy(model)


@pytest.mark.parametrize(
    "values, message",
    [
        ([[0, 0, -1, -1, -1]], "shape"),
        ([0, 0, -1], "shape"),
        ([0, float("nan"), -1, -1, -1], "finite"),
        ([2, 0, -1, -1, -1], "<= 1"),
    ],
)
def test_checkpoint_policy_rejects_malformed_predictions(numeric_model, values, message):
    model, np, _ = numeric_model
    model.action = np.asarray(values)
    with pytest.raises(ValueError, match=message):
        StableBaselinesPolicy(model)(observation())


def test_checkpoint_policy_checks_ray_count_before_predicting(numeric_model):
    model, _, _ = numeric_model
    with pytest.raises(ValueError, match="16 rays"):
        StableBaselinesPolicy(model)(observation(rays=list(observation().rays[:4])))
    assert model.calls == []


def test_checkpoint_policy_rejects_float32_overflow_before_predicting(numeric_model):
    model, _, _ = numeric_model
    with pytest.raises(ValueError, match="finite float32"):
        StableBaselinesPolicy(model)(observation(powerup_count=10 ** 40))
    assert model.calls == []


@pytest.mark.parametrize("algorithm", ["ppo", "a2c"])
def test_checkpoint_load_supports_shared_trainer_algorithms(numeric_model, monkeypatch, tmp_path, algorithm):
    from qa_framework.policies import stable_baselines

    model, _, _ = numeric_model
    calls = []

    def load(path, *, device):
        calls.append((path, device))
        return model

    original_import = stable_baselines.import_module
    sb3 = SimpleNamespace(**{algorithm.upper(): SimpleNamespace(load=load)})
    monkeypatch.setattr(
        stable_baselines, "import_module",
        lambda name: sb3 if name == "stable_baselines3" else original_import(name),
    )
    checkpoint = tmp_path / "gameplay.zip"
    checkpoint.write_bytes(b"stub checkpoint; loader replaced in this test")
    policy = StableBaselinesPolicy.load(checkpoint, algorithm=algorithm, device="cpu")
    assert policy.model is model
    assert calls == [(str(checkpoint), "cpu")]


def test_checkpoint_load_reports_missing_path_and_unsupported_algorithm(tmp_path):
    with pytest.raises(FileNotFoundError, match="checkpoint does not exist"):
        StableBaselinesPolicy.load(tmp_path / "missing.zip")
    with pytest.raises(ValueError, match="algorithm"):
        StableBaselinesPolicy.load(tmp_path / "missing.zip", algorithm="dqn")


def test_checkpoint_load_explains_missing_optional_dependency(monkeypatch, tmp_path):
    from qa_framework.policies import stable_baselines

    def missing(_name):
        raise ImportError("not installed")

    monkeypatch.setattr(stable_baselines, "import_module", missing)
    checkpoint = tmp_path / "gameplay.zip"
    checkpoint.write_bytes(b"unused")
    with pytest.raises(ImportError, match=r"qa-framework\[inference\]"):
        StableBaselinesPolicy.load(checkpoint)
