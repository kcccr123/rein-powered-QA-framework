# Future PCG, QA and Unity connection

This preparation changes only QA. There is no Unity transport, socket client,
process launcher, PCG policy invocation, trainer wiring or gameplay controller in
this package. The executable examples use `FakeQAEnvironment`. Their completion
results are synthetic contract checks and cannot establish game playability.

## What the repositories currently provide

| Component | Current implementation | Work needed for a real connection |
| --- | --- | --- |
| Game PCG recipe | `PcgGenerationParameters` contains eight integer fields, listed below. `Generator.RunProgram(parameters)` generates directly in Unity. | A game-side handoff must accept the selected candidate, preserve its identity and acknowledge the loaded content. |
| PCG policy | `PcgPolicyRunner.Decide(seed)` chooses seeded random parameters. | Implement and train the PCG policy using the shared RL framework. |
| PCG environment | `PcgRlEnvironmentScaffold` is a `MonoBehaviour` with zero-sized placeholders. | Implement a real PCG environment and define its own observation/action contract. |
| Gameplay QA | `PcgDungeonManager` has commented QA-player wiring. No controller consumes the input action asset. | Provide the player controller, ray sensors, game outcomes and metrics. Establish actual control semantics. |
| Game assets/scene | Checked-in settings have no player prefab, enemies or bosses. The sample scene lacks generator/QA/RL host wiring. | Supply playable setup and scene/runtime wiring. |
| Shared RL transport | `RlProtocol` carries numeric observations/actions, reward and `DONE`; reset accepts a seed. | Add level handoff, explicit schema/role agreement, outcome evidence and metrics through a future backend/protocol extension. |
| Shared trainer | `train.py` constructs one `UnityGymEnv` and trains PPO/A2C. | Select the QA Gym environment for QA training, and coordinate PCG generation with QA evaluation outside this package. |

Source locations for this audit:

- [PCG parameter fields](../../kawaii-power-little-adventure/Assets/PCGDungeon/Scripts/Generation/PcgGenerationParameters.cs)
- [Generation entry point](../../kawaii-power-little-adventure/Assets/PCGDungeon/Scripts/Generation/Generator.cs)
- [Generation settings](../../kawaii-power-little-adventure/Assets/PCGDungeon/Scripts/Data/GenerationSettings.cs)
- [Current policy](../../kawaii-power-little-adventure/Assets/PCGDungeon/Scripts/Policy/PcgPolicyRunner.cs)
- [Manager and commented QA handoff](../../kawaii-power-little-adventure/Assets/PCGDungeon/Scripts/Generation/PcgDungeonManager.cs)
- [Game RL scaffold](../../kawaii-power-little-adventure/Assets/PCGDungeon/Scripts/RL/PcgRlEnvironmentScaffold.cs)
- [Game settings asset](../../kawaii-power-little-adventure/Assets/PCGDungeon/Settings/Settings.asset)
- [Shared wire protocol](../../unity-reinforcement-learning/Runtime/RlProtocol.cs)
- [Shared Python environment](../../unity-reinforcement-learning/PythonEnv/unity_env.py)
- [Shared trainer](../../unity-reinforcement-learning/PythonEnv/train.py)

## QA handoff and feedback

`LevelSpec` is a versioned, deeply immutable generation recipe with `level_id`,
`generation_seed`, `generation_parameters` and optional `generator_version`.
`EpisodeConfig.level` carries it through the existing `QAEnvironment.reset` API.
The generation seed and the QA episode seed are separate values: repeated QA
rollouts may evaluate one candidate using different agent/environment randomness.

Current Unity parameter names are `RoomCount`, `AreaWidth`, `AreaHeight`,
`MinRoomWidth`, `MaxRoomWidth`, `MinRoomHeight`, `MaxRoomHeight` and `Seed`.
`level.pcg_parameters()` copies the supplied parameters and includes `Seed`.
A supplied `Seed` must match `generation_seed`, and both seeds for an evaluation
fit nonnegative 32-bit integers. QA preserves parameter names and values rather
than owning generation rules. The future game backend must validate the fields
and ranges it supports. The currently commented `EnemyDensity`, `ContentDensity`
and `AdjacentRoomConnections` fields are not supported game parameters.

A recipe is not a dungeon snapshot. The game has no serialized dungeon exporter
or importer. Replaying generation also requires matching `GenerationSettings`,
prefabs and game/generator revision; record those revisions in `generator_version`
or serialized episode metadata. An empty parameter mapping is reference context
only; a future backend must reject it unless it can resolve that level identity.

`EvaluationRequest` adds a caller-assigned `request_id`, QA seed, policy/checkpoint
ID and limits. `evaluate_episode(environment, policy, request)` invokes the supplied
policy on typed observations and returns an `EvaluationReport` containing:

- The complete request and candidate provenance.
- Explicit termination/truncation flags and `TerminalReason`.
- Final `EpisodeMetrics`, including completion, death, step count, elapsed time,
  damage, kills, powerups and stuck events.
- Total QA reward, the environment implementation name, and optional infrastructure
  error details.

The request, report, level, config, metrics and result expose `to_dict`/`from_dict`
for finite JSON handoffs. Report and level envelopes carry their schema versions;
unknown versions are rejected. No PCG reward or acceptance threshold is imposed.
The external orchestration owns how QA feedback affects PCG evaluation or learning.

`FakeQAEnvironment` echoes the requested identity and seed for offline tests.
It does not read the recipe, generate geometry, run a player, or measure combat.
Zero game-event metrics also mean unobserved values; a real adapter must document
which measurements it supplies. Keep `environment_name` in stored reports so
synthetic results remain identifiable.

Run the complete offline handoff:

```shell
python examples/evaluate_generated_level.py
```

## Optional shared RL interface

Install `.[rl]` to use
`qa_framework.environments.gymnasium.GymnasiumQAEnvironment`. The core package
still needs only the standard library. This wrapper requires an explicitly supplied
QA environment and creates no Unity transport.

```python
from qa_framework import EpisodeConfig, FakeQAEnvironment
from qa_framework.environments.gymnasium import GymnasiumQAEnvironment

env = GymnasiumQAEnvironment(
    FakeQAEnvironment(),
    max_ability_slot=0,
    default_config=EpisodeConfig(max_steps=100),
)
try:
    observation, info = env.reset(seed=123)
    while True:
        action = env.action_space.sample()
        observation, reward, terminated, truncated, info = env.step(action)
        if terminated or truncated:
            break
finally:
    env.close()
```

The wrapper implements the [Gymnasium environment API](https://gymnasium.farama.org/api/env/)
and the continuous Box interface accepted by the shared trainer's PPO/A2C path.
It can be supplied to the existing algorithm/Monitor/VecEnv pattern later; the
shared trainer currently constructs its socket environment directly, so installing
QA alone does not select it. See the [Stable Baselines3 custom environment contract](https://stable-baselines3.readthedocs.io/en/master/guide/custom_env.html).

Observations use existing schema v1: `7 + 7 * ray_count` float32 values (119 by
default). Counts and the previous ability slot remain raw observation values.
Action encoding v1 uses five continuous components in `[-1, 1]`:

| Index | QA command | Decoding |
| --- | --- | --- |
| 0, 1 | `move_x`, `move_y` | Preserve bounded per-axis movement. |
| 2 | `attack` | Positive presses; zero/negative releases. |
| 3 | `interact` | Positive presses; zero/negative releases. |
| 4 | `ability` | Map evenly spaced values from -1 to 1 to slots 0 through `max_ability_slot`; round ties upward. Slot 0 means none. |

Canonical no-op is `(0, 0, -1, -1, -1)`. With `max_ability_slot=0`, the fifth
component always decodes to no ability. `QAActionCodec` provides the same encoding
without NumPy/Gymnasium. This is a QA policy contract; the game has no matching
controller/vector mapping yet. A future Unity backend must agree to these commands
and supported slots explicitly. PCG uses its own policy and action semantics.

Reset options accept `{"config": EpisodeConfig(...)}`, including its selected
level. Omission uses the constructor's default config. Unknown options are rejected
before the supplied environment changes state. A policy used by `evaluate_episode`
can decode its normalized predictions through `QAActionCodec` after observing
`flatten_observation(observation)`; initialize recurrent policy state per request
in the external policy caller.

## Future Unity backend requirements

Implement `QAEnvironment` behind the QA boundary when connection work is authorized.
`UnityQARequirements` and `UnityQACapabilities` are offline capability records.
`require_ready` rejects missing evidence for the gameplay QA role, schema versions,
ray count, vector dimensions, supported ability slots, level handoff, identity
acknowledgement, terminal reasons, metrics and separate termination flags.
These records are not a handshake already implemented by Unity. Obtain them from
verified backend behavior; filling all flags with `True` cannot create those features.

The current shared bridge exchanges:

```text
CONFIG:OBS=<n>;ACT=<n>;ENV_TYPE=SINGLE
ACT=RESET;SEED=<seed>
ACT=<bounded numeric vector>
OBS=<numeric vector>;REW=<reward>;DONE=<0 or 1>
```

This cannot pass `LevelSpec` or return QA evidence. Matching observation/action
sizes is insufficient. The existing `UnityGymEnv` ignores reset options and returns
empty info. Passing it directly as gameplay QA would lose the selected level and
the distinction between completion, death and truncation.

A future backend must fulfill this sequence:

1. Agree explicitly on the gameplay QA role, observation and action encoders,
   ray ordering, hit classifications, ability slots and simulation step timing.
2. Load or regenerate the exact requested candidate before reset succeeds. Reject
   unsupported recipes/settings instead of generating unrelated content. Acknowledge
   `level_id` and `generation_seed` in reset info and every step's info; preserve
   the full recipe and revision context in the request/report.
3. Reset the player, sensors, episode counters and previous action. Apply one valid
   QA command per step and expose cumulative `EpisodeMetrics` snapshots.
4. On an ending step, return a typed `info["episode_result"]` with an explicit reason
   and metrics matching `last_metrics`. Completion and death terminate; imposed
   limits and environment failures truncate. Report completion only from an actual
   game completion signal. Reward, `DONE`, generation success and validator acceptance
   are insufficient evidence of completion.
5. Enforce step/duration limits and deadlines for reset, individual actions and
   transport reads. Translate infrastructure errors into `QAEnvironmentError`;
   the evaluator records `ENVIRONMENT_FAILURE` using the last confirmed metrics
   and attempts to close that session. Cleanup failures remain in the error details.
   Policy exceptions and malformed adapter outputs propagate
   as implementation problems. Normal completion keeps the environment reusable.

Generation failure precedes QA gameplay. Keep it in the PCG/generation status;
do not report it as player death or failed level completion. The current
`DungeonValidator` checks structural existence/spawn selection, not reachability.

The evaluator checks candidate acknowledgement, consistent actions, metrics and
terminal results, and bounds the number of rollout calls by `max_steps`. It cannot
interrupt a blocked backend call, so individual operation deadlines belong to
the future transport/game backend. PCG and QA coordination, separate checkpoints,
policy updates and acceptance rules belong to the shared RL/orchestration layer.

## Verification

The tests exercise JSON round trips, candidate/seed acknowledgement, completion
and death evidence, limits, infrastructure failure, action encoding and the
optional Gymnasium lifecycle using fake environments. The Gymnasium checker test
uses a fixture omitting the fake's sequential diagnostic `episode_id` for seeded
info comparisons; production info retains that identifier. No check establishes
compatibility with a running Unity game or trains either policy.
