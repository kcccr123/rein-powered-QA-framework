# Unity gameplay QA integration

QA connects to the game's existing `QaTcpHost` using newline-delimited JSON over
TCP. Unity generates the requested dungeon and owns player controls, raycasts,
combat and outcomes. Python processes typed observations, chooses actions,
evaluates complete episodes and selects the next level recipe. No sibling
repository changes are required for these QA features.

## Run rounds

From this repository, using Python 3.10+:

```shell
python -m pip install -e ".[test]"
python -m pytest
python examples/play_generated_level_live.py --fake --episodes 3
```

`--fake` runs synthetic contract fixtures: completion does not prove gameplay or
playability. For live QA, open `InitialGenerationScene` in the sibling game,
ensure `QaTcpHost` is configured with the generator/validator, and press Play.
The console should show `QaTcpHost listening on 127.0.0.1:8800`.

```shell
python examples/play_generated_level_live.py --episodes 10 --seed 42 --max-steps 1000 --report-path artifacts/qa-rounds.jsonl
```

Use `--episodes 0` for indefinite rounds, `--host`/`--port` for the QA endpoint,
and `--timeout-seconds` for each network operation. `--max-duration-seconds`
limits simulated episode time. Ctrl+C closes the connection. Each completed
round is flushed as a separate JSONL record; infrastructure failure records its
error and exits with status 1. `--run-id` distinguishes runs and
`--generator-version` records game/settings revision context.

The baseline `RayHeuristicPolicy` seeks visible exits, then loot, then enemies;
it selects clear space when no target is visible, attacks/interacts at close
range and retreats from visible enemies at low health. It has no global map or
pathfinding, so it may loop or fail to finish a reachable level.

## Rays, health and model inference

The existing socket decoder constructs `QAObservation` from `observation.rays`,
`observation.health_01`, powerup count and previous action. The live example now
passes each observation into its policy before the next action.

Current Unity sensor geometry is world aligned:

| Ray index (16 rays) | World direction | QA movement |
| --- | --- | --- |
| 0 | +Z | `(0, 1)` |
| 4 | +X | `(1, 0)` |
| 8 | -Z | `(0, -1)` |
| 12 | -X | `(-1, 0)` |

For index `i` and `N` rays, direction is `(sin(2*pi*i/N), cos(2*pi*i/N))` in
`(move_x, move_y)`. Rays do not rotate with the player. The current sensor range
is 12 world units, so normalized distance 0.1 is approximately 1.2 units. Keep
this geometry consistent with policy training. The handshake advertises 16 rays;
every received observation is checked against the negotiated requirement.

The sibling RL trainer uses Stable-Baselines3 PPO/A2C and saves `.zip`
checkpoints. `StableBaselinesPolicy` can run a gameplay QA checkpoint locally:

```shell
python -m pip install -e ".[inference,test]"
python examples/play_generated_level_live.py --episodes 10 --model-path artifacts/qa/final_model.zip --algorithm ppo
```

The adapter validates the model spaces, encodes the observation as float32 using
`flatten_observation`, calls `predict(..., deterministic=True)`, and decodes the
result with `QAActionCodec`. Observation schema v1 has `7 + 7*N` values (119 at
16 rays): health, raw powerup count, previous action, then hit/distance/type
one-hot values for every ordered ray. Outputs must be a five-component floating
Box in `[-1, 1]`: movement X/Y, attack, interact, ability. The current Unity
handshake supports no abilities (`max_ability_slot=0`).

Dimensions do not prove semantic compatibility. Use a checkpoint trained with
this exact QA encoding and sensor geometry. The existing game PCG checkpoints
produce generation controls, not player actions; their dimensions differ and
the adapter rejects them. No trained gameplay QA checkpoint is included.
External observation normalization and recurrent models are not supported by
this feedforward adapter. Importing the core package or using the baseline
does not require NumPy, Gymnasium, Torch or SB3.

## Train a QA gameplay checkpoint

The sibling `PythonEnv/train.py` constructs its own `UnityGymEnv` for the
separate RL bridge; it does not select this JSON gameplay environment. A local
example supplies QA's Gymnasium wrapper to the same PPO/A2C algorithms:

```shell
python examples/train_unity_qa.py --algorithm ppo --total-timesteps 100000 --output artifacts/qa/final_model.zip
python examples/play_generated_level_live.py --model-path artifacts/qa/final_model.zip --episodes 10
```

Start the same Unity scene first. Training repeatedly resets a fixed complete
recipe through `GymnasiumQAEnvironment(UnityQAEnvironment(...))`, learns a
feedforward `MlpPolicy`, saves an SB3 checkpoint, and closes its session. A2C is
also available with `--algorithm a2c`. Pass the matching algorithm at inference.
`--fake` exercises training plumbing only, and its resulting model is synthetic.
Training against one recipe and sparse completion reward is a starting point;
it does not establish a robust policy across generated levels. Training duration
and gameplay quality have not been validated here. Learning remains in the
example and optional external library, outside the core QA package.

## Adaptive round loop

`iter_adaptive_rounds` runs each bounded `evaluate_episode` to completion, death,
step limit or duration limit before constructing the next `LevelSpec`:

```python
from qa_framework import EpisodeConfig, LevelSpec, UnityQAEnvironment
from qa_framework.orchestration import iter_adaptive_rounds
from qa_framework.policies import RayHeuristicPolicy

baseline = LevelSpec("qa-level", 42, {
    "RoomCount": 10, "AreaWidth": 100, "AreaHeight": 100,
    "MinRoomWidth": 5, "MaxRoomWidth": 10,
    "MinRoomHeight": 5, "MaxRoomHeight": 10,
})
env = UnityQAEnvironment()
try:
    for report in iter_adaptive_rounds(
        env, RayHeuristicPolicy(), baseline, rounds=10,
        config=EpisodeConfig(max_steps=1000), policy_id="ray-heuristic-v1",
    ):
        print(report.state_after, report.next_level)
finally:
    env.close()
```

Pass `rounds=None` to stream indefinitely. An optional policy `reset_episode()`
hook runs before each evaluation. A round report records the evaluation,
before/after `AdaptiveState`, `skill_signal`, next recipe, original baseline and
adaptation settings. `to_dict`/`from_dict` preserve and validate these decisions.
To resume, pass the original baseline, report's `adaptation` and `state_after`,
and the same original QA seed/run ID. The next recipe can also be inspected or
evaluated separately.

The controller is an explicit heuristic:

1. Successful completion scores `1 - 0.5 * budget_fraction`, where the fraction
   is the larger used proportion of step and optional duration budgets.
2. Capped damage and stuck-event penalties are subtracted, then the score is
   clamped to `[0, 1]`. Defaults subtract up to 0.25 each, using damage scale 10
   raw health units and stuck scale 5 events. Damage is not `health_01`.
3. Death and budget truncation score zero. An exponential average with default
   update rate 0.25 updates the skill estimate. Difficulty moves toward that
   estimate by at most 0.1 per round and remains in `[0, 1]`.
4. Room count and area dimensions interpolate around the original recipe within
   `AdaptiveDifficultyConfig` bounds. Default bounds are 6..14 rooms and 80..120
   per area axis, with the default recipe at difficulty 0.5. Room dimensions
   remain fixed. Seeds advance independently of the feedback decision.

Choose bounds compatible with the game's generation settings; the baseline must
include all seven geometry fields and lie inside those bounds. Larger geometry
is assumed to increase navigation demand. This is neither a trained PCG policy
nor a validated human-skill measurement, and generated geometry may still fail
validation. The currently unsupported `EnemyDensity`, `ContentDensity` and
`AdjacentRoomConnections` fields are never added.

Infrastructure failure provides no skill signal, holds calibration and stops
after yielding its report because the evaluator closes that session. Policy
exceptions and malformed adapter output propagate instead of becoming level
difficulty evidence. Reward alone never establishes success.

## Current wire contract and metrics

Unity hosts the listener; Python connects without launching Unity. Unity first
sends a `UnityQACapabilities` JSON object declaring gameplay role, schema/action
encoding version 1, 16 rays, 119 observation values, five action values, no
abilities, and support for recipes, level acknowledgement, explicit terminal
reasons, metrics and separate termination flags. Incompatible hosts are rejected.

Every subsequent request is one JSON line answered by one JSON line:

| Command | Request | Reply |
| --- | --- | --- |
| `reset` | Seed, episode limits, level identity and concrete generation parameters | `ok`, serialized observation, info |
| `step` | `action` with `move_x`, `move_y`, `attack`, `interact`, `ability` | `ok`, observation, reward, terminated, truncated, info |
| `close` | Command only | Acknowledgement; the client closes without awaiting it |

Optional reset fields use explicit boolean flags for Unity's `JsonUtility`:
`seed_provided`, `max_duration_provided`, `level_provided` and
`generator_version_provided`. `generation_parameters` includes the seven
geometry fields plus `Seed`. `LevelSpec.pcg_parameters()` supplies a matching
seed without mutating the original recipe. The game generates and validates
the requested recipe before acknowledging reset.

Info includes `schema_version`, `step`, `episode_id`, `level_acknowledged`,
`level_id`, `generation_seed`, `has_episode_result` and `episode_result`.
False acknowledgement/result flags remove their placeholder fields when
decoded. Ending results use the existing `EpisodeResult`/`EpisodeMetrics`
serialization. Completion/death terminate; step/time limits truncate. Errors
are `{"ok": false, "error": "..."}` and become `QAEnvironmentError`.

The current host only sends event metrics in the ending result. Intermediate
`last_metrics` snapshots use the confirmed `info.step` and the controller's
fixed 0.1 simulated seconds per step; event fields remain unobserved zero
defaults. `info["metrics_source"]` distinguishes `step_bookkeeping` from
`unity_episode_result`. Set the adapter's `step_duration_seconds` if the
controller changes; this value is not negotiated. Final event measurements
come from Unity, and float32 elapsed-time rounding is reconciled to the matching
step clock (`reported_elapsed_seconds` retains the original). Movement
acknowledgements allow float32 rounding before restoring the sent action in
`previous_action`. Boolean flags, ability slots and step counters remain strict.

Socket reads/writes have timeouts. Bad frames retire the session. Evaluation
checks level acknowledgement, action consistency, step counts, limits and
explicit final evidence. A lost connection preserves the last confirmed
progress; unreported combat metrics cannot be recovered from health alone.

## Known game-side limitations

These remain outside the QA repository:

- `QaTcpHost.HandleStep` checks `remotePlayer.EpisodeActive` before examining
  final metrics. Physics callbacks can mark death/exit between requests, causing
  `No active remote episode` instead of a terminal result. QA records an
  environment failure and stops. Correcting the host requires a separate
  authorized game change; QA cannot reconstruct an unreported outcome.
- With a supplied recipe, the game uses `generation_seed` and overrides the QA
  reset seed. Reports retain both, but the current game does not implement
  independent QA randomness.
- `generator_version` is recorded by QA but not enforced by the current host.
  A recipe alone does not snapshot prefabs, settings or geometry.
- Elapsed metrics use simulation steps; enemy contact damage uses real frame
  time. Execution speed can therefore affect gameplay. Generation validation
  does not establish reachability or playability.

Source references: [QA host](../../kawaii-power-little-adventure/Assets/PCGDungeon/Scripts/QA/Transport/QaTcpHost.cs),
[wire records](../../kawaii-power-little-adventure/Assets/PCGDungeon/Scripts/QA/Transport/QaWireMessages.cs),
[player sensors/controller](../../kawaii-power-little-adventure/Assets/PCGDungeon/Scripts/QA/QaPlayerController.cs),
[PCG fields](../../kawaii-power-little-adventure/Assets/PCGDungeon/Scripts/Generation/PcgGenerationParameters.cs),
[shared trainer](../../unity-reinforcement-learning/PythonEnv/train.py).

## Verification scope

Tests cover schema/serialization, synthetic evaluations, a real local TCP test
host mirroring the JSON protocol, float32 acknowledgement/duration boundaries,
policy inference contracts, adaptive feedback/bounds, failure handling and report
resumption. Optional training/inference paths use API-compatible test doubles
when SB3 is unavailable. No test here launches Unity, trains a useful gameplay
policy, or proves live dungeon completion.
