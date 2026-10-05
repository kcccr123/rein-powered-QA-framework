# QA Framework

A lightweight Python environment abstraction for automated gameplay QA and an
external reinforcement learning framework. Currently this project provides typed data
models, validation, a deterministic fake environment, and tests. 

The learning algorithms belong to the external RL framework, the game simulation
belongs to Unity, and procedural content generation belongs to the separate
Unity PCG framework.

## Architecture

```text
External RL Framework
        |
        v
QAEnvironment
        |
        +----------------------+
        |                      |
        v                      v
FakeQAEnvironment      Future UnityQAEnvironment
                               |
                               v
                         ML-Agents transport
                               |
                               v
                             Unity
```

External consumers use `QAEnvironment` and the domain models. They do not import
Unity, ML-Agents, transport objects, or game-specific code. `QAEnvironment` is an
abstract base class (`abc.ABC`): explicit inheritance makes implementations easy
to discover and prevents instantiating an adapter that omits required methods.
Its return conventions also allow a future Gymnasium wrapper without adding
Gymnasium as a dependency.

The source layout separates these concerns:

- `models/`: actions, observations, ray perception, episode configuration, metrics.
- `environments/`: the abstract contract and fake implementation.
- `schema/`: schema constants, validation, and flattening.
- `evaluation/result.py`: descriptive episode outcomes, without acceptance rules.
- `environments/unity.py`: a documented extension point for a future adapter.

## Install and run

Use Python 3.10 or newer, from the repository root:

```shell
python -m pip install -e ".[test]"
python -m pytest
python examples/random_agent.py
```

The `test` extra installs pytest. Runtime code uses only the standard library.

## Environment API

```text
reset(*, seed: int | None = None, config: EpisodeConfig | None = None)
    -> tuple[QAObservation, dict[str, object]]

step(action: QAAction)
    -> tuple[QAObservation, float, bool, bool, dict[str, object]]

close() -> None
```

The two booleans returned by `step` are `terminated` and `truncated`, in that
order. `terminated` describes an environment outcome such as completion or
player death. `truncated` describes an imposed limit such as maximum steps or
duration, or an environment failure. Consumers stop the episode when either is true.

The interface also exposes `observation_schema_version`, `episode_active`, and
`last_metrics`. Metrics are immutable snapshots of the current or most recent
episode. Reset starts a fresh episode and clears metrics and the previous action.
Calling `step` before reset or after the episode ends raises `RuntimeError`.
The fake environment's `close` is idempotent and permanently closes the instance;
reset and step then raise `RuntimeError`.

```python
from qa_framework import FakeQAEnvironment, QAAction, QAEnvironment

env: QAEnvironment = FakeQAEnvironment()
try:
    observation, info = env.reset(seed=123)
    while True:
        # An external policy can choose an action from the typed observation.
        action = QAAction(move_x=1.0)
        observation, reward, terminated, truncated, info = env.step(action)
        if terminated or truncated:
            break
finally:
    env.close()
```

See [examples/random_agent.py](examples/random_agent.py) for a random policy using
the same API. The example performs no learning.

## Observation schema version 1

`OBSERVATION_SCHEMA_VERSION = 1` identifies the encoder contract. A
`QAObservation` is an immutable dataclass containing:

| Field | Contract |
| --- | --- |
| `health_01` | Finite float in `[0, 1]` |
| `powerup_count` | Nonnegative integer, retained as a raw count |
| `rays` | Tuple of exactly `ray_count` `RayObservation` values |
| `previous_action` | Last applied `QAAction`; `QAAction.noop()` at reset |
| `ray_count` | Positive schema count; defaults to `DEFAULT_RAY_COUNT = 16` |

The default schema always has 16 rays. An explicit alternate `ray_count` is
supported for an agreed schema configuration; producers and consumers must agree
on that count. There are no variable lists of nearby enemies, loot, or objects.
Ray index ordering is stable; the eventual adapter must document its spatial
directions and keep them consistent across episodes.

Each ray contains a strict boolean `hit`, a finite `distance_01` in `[0, 1]`, and
a `QAObjectType`: `NONE`, `OBSTACLE`, `ENEMY`, `LOOT`, or `EXIT`. A ray with
`hit=False` must use `NONE`; its conventional full-range distance is `1.0`.
Validation accepts other normalized distances for a no-hit ray. `hit=True` with
`NONE` represents a hit without a classified semantic type. `NONE` therefore
does not by itself imply that no physical hit occurred.

After `step(action)`, the returned observation's `previous_action` equals the
validated action supplied to that step. Reset replaces it with the no-op action.

Construction and validation reject invalid ranges, wrong ray counts, negative
counts, inappropriate field types, NaN, and infinity. Immutable tuples and frozen
dataclasses protect the observation and action contract from accidental mutation.

## Action schema

`QAAction` is an immutable dataclass:

| Field | Default | Contract |
| --- | --- | --- |
| `move_x` | `0.0` | Finite float in `[-1, 1]` |
| `move_y` | `0.0` | Finite float in `[-1, 1]` |
| `attack` | `False` | Boolean |
| `interact` | `False` | Boolean |
| `ability` | `0` | Nonnegative integer; `0` means none, positive values are slots |

`QAAction.noop()` returns the all-default action. Movement is validated per axis;
diagonal normalization and physical movement semantics belong to the eventual
environment implementation. The contract has no game-specific ability catalog.
An adapter with a known slot count can call
`validate_action(action, max_ability_slot=N)` to reject unsupported slots.

`action_to_tuple` and `action_from_tuple` use the exact order
`(move_x, move_y, attack, interact, ability)` and preserve boolean/integer fields.
They do not encode an ML-Agents transport action.

## Flattening and serialization

`flatten_observation(observation)` returns a tuple of floats, with this exact
ordering:

```text
health_01, raw powerup_count,
previous_action.move_x, previous_action.move_y,
previous_action.attack, previous_action.interact, previous_action.ability,
ray_0.hit, ray_0.distance_01, ray_0.NONE, ray_0.OBSTACLE,
    ray_0.ENEMY, ray_0.LOOT, ray_0.EXIT,
ray_1.hit, ray_1.distance_01, ray_1.NONE, ray_1.OBSTACLE,
    ray_1.ENEMY, ray_1.LOOT, ray_1.EXIT,
...remaining rays in index order...
```

Flags become `0.0` or `1.0`; semantic types use a five-element one-hot vector in
the stated order. Powerup count and ability slot are raw numeric values, with no
hidden normalization. `get_flat_observation_size(ray_count=16)` returns
`7 + 7 * ray_count`, which is **119** for the default schema. No NumPy is needed.

Actions and observations expose `to_dict()` and `from_dict()` for JSON-compatible
primitive serialization. Observation dictionaries include `schema_version`,
`ray_count`, player fields, nested ray records, and the previous action. Decoding
validates the contract and rejects unsupported schema versions. There is no
schema migration system in this milestone.

## Episodes, metrics, and info

`EpisodeConfig` contains `max_steps` (default `1000`), optional
`max_duration_seconds`, and caller-defined `metadata`. Limits must be positive.
Metadata is shallow-copied into a read-only mapping; callers should treat any
nested values as immutable. It may eventually identify an externally generated
level without defining PCG parameters or generation behavior.

`EpisodeMetrics` describes `step_count`, `elapsed_seconds`, `completed`,
`player_died`, `enemies_killed`, `damage_taken`, `powerups_used`, and
`stuck_events`. Unobserved values retain sensible zero/false defaults. These are
measurements; they do not decide whether a level is acceptable.

`EpisodeResult` contains `terminated`, `truncated`, `terminal_reason`, and
`metrics`. `TerminalReason` includes `COMPLETED`, `PLAYER_DIED`, `MAX_STEPS`,
`MAX_DURATION`, and `ENVIRONMENT_FAILURE`.

The info dictionary carries auxiliary episode context rather than core
observations. The fake environment returns `schema_version`, `step`, and
`episode_id` on reset and every step. Its episode IDs are local, monotonically
increasing identifiers. An ending step also includes `episode_result`, an
`EpisodeResult` object. Consumers should use the typed observation for policy
input and the explicit booleans to detect episode endings.

## Fake environment behavior

`FakeQAEnvironment` defaults to `completion_steps=20`, `ray_count=16`, and
`step_duration_seconds=0.1`. Seeded observation generation uses local randomness
without changing global random state. The same seed, configuration, and actions
produce the same observations, rewards, and metrics. Episode IDs identify resets
and are bookkeeping rather than part of the seeded observation sequence.

Each valid step records the supplied action, increments the counter, advances the
simulated clock, and produces a fixed-size observation. Reward is `0.0` during
the episode and `1.0` on successful completion. `max_steps` and
`max_duration_seconds` can truncate it earlier. Completion takes precedence if a
limit coincides with the completion step. Invalid actions leave episode state
unchanged.

Elapsed time is simulated as `step_count * step_duration_seconds`, not measured
from wall-clock time. This makes duration limits and tests deterministic. The
fake environment is a contract fixture rather than a gameplay simulation, and
does not populate combat or game-specific metrics.

## Future Unity adapter

`qa_framework.environments.unity` currently documents the extension point and
implements no integration. A future `UnityQAEnvironment` will inherit
`QAEnvironment`, translate transport observations into validated
`QAObservation` values, translate `QAAction` into transport actions, and report
episode outcomes through the same lifecycle. It can adapt
`mlagents_envs.UnityEnvironment` or a different transport while keeping those
dependencies behind the environment boundary.

Unity owns raycasts, actual movement, and game simulation. The separate PCG
framework owns generated content. A future adapter must agree with the Python
schema on ray count, ray ordering, semantic classifications, previous-action
behavior, and numeric ranges. This package currently has no ML-Agents dependency
and starts no Unity process.
