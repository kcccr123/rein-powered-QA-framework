# QA Framework

A reusable Unity unit adapter, Python QA evaluator, and container trial runtime with confirmed episode storage.

See [Platform refactor](platform-refactor.md) for bounded parallel trial execution, modular testing, and the planned aggregated reporting design.

The standalone integration keeps Unity in charge of starting its Python consumer:

```text
+------------------------------------------------------------------+
| SCENE STARTUP                                                    |
| Launcher starts Python -> validate TCP readiness -> enable game. |
+------------------------------------------------------------------+
             |
             v
+------------------------------------------------------------------+
| DEVELOPER: unit lifecycle                                        |
| StartUnit -> run game -> EndUnit.                                |
| Aggregate final actor snapshots in OnUnitEnd.                    |
+------------------------------------------------------------------+
             |
             v
+------------------------------------------------------------------+
| SHARED QA TRANSPORT                                              |
| Inherited SendReport -> JSON over the same TCP connection.       |
+------------------------------------------------------------------+
             |
             v
+---------------------------------------------------------------------+
| DEVELOPER: Python consumer                                          |
| Receive -> evaluate named requirements -> optionally store results. |
+---------------------------------------------------------------------+
```

The developer calls `StartUnit(unitId)` and `EndUnit()`. The adapter invokes developer hooks and sends the final report. It does no per-tick state collection or end-condition polling. Unity owns actors, simulation, model inference, and unit lifecycle; the shared RL framework owns training.

## Initial package setup

Install Python and this Python package into your chosen environment:

```shell
python -m pip install -e path/to/rein-powered-QA-framework
```

Install the Unity package from `Unity/package.json` through Package Manager. Supply the interpreter, consumer script, and packages on each test machine, including desktop builds.

## Run a containerized suite

Install the optional YAML dependency and start a local Docker engine with Compose:

Copy [suite.example.yaml](suite.example.yaml) to `suite.yaml` and fill in the image, episode count, concurrency limit, output directory, environments, and agents. The template explains each field and includes two environments and two agents.

```shell
python -m pip install -e ".[platform]"
qa-run-suite suite.yaml
```

```yaml
image: my-trial-image:tag
episodes_per_trial: 100
max_concurrent_trials: 4
output_dir: ./runs
model_preparation:
  unity_editor: C:/Program Files/Unity/Hub/Editor/6000.3.4f1/Editor/Unity.exe
  timeout_seconds: 600

environments:
  - id: dungeon
    scene: Dungeon

agents:
  - id: baseline
    model: models/baseline.onnx
```

- Counts must be positive integers. Both lists must be nonempty and have unique, nonempty string IDs within each list.
- One fresh container runs each environment/agent pair, with at most `max_concurrent_trials` active at once in one Compose project.
- The supplied image keeps its entrypoint and command. It reads `QA_TRIAL_CONFIG`, a read-only JSON file with `suite_id`, `trial_id`, `episodes_per_trial`, `environment`, and `agent`, and writes artifacts to `QA_OUTPUT_DIR`.
- `model_preparation.unity_editor` must name an existing Unity Editor executable; `timeout_seconds` is a positive integer, default 600. Install `qa-framework[platform]` on the host for YAML and ONNX validation.
- Each `agents[].model` must be an existing, self-contained ONNX file. Editor, model, and output paths resolve beside the YAML when relative. External weights are rejected. Other settings remain JSON pass-through.
- Before any trial, an isolated temporary Unity project imports each distinct resolved ONNX path once with Inference Engine **2.4.1** and exports `.sentis`. Artifacts, `manifest.json`, `preparation.log`, and `unity.log` remain in `<suite-id>/models/`; the temporary project is removed after its Editor exits. There is no cross-suite cache. Failure/timeout stops the suite; cancellation stops the owned Editor and returns 130.
- Each selected artifact is mounted read-only at `/qa/model/agent.sentis`; the generated assignment uses that path without changing the YAML or loaded agent settings.
- `environments[].scene` is required: use a full build scene path or a unique scene name. Build a `QaPlatformBootstrap` scene first, followed by all supported gameplay scenes. The bootstrap selects the scene and requires exactly one active, enabled `QaPlatformAdapter` before gameplay starts.
- `output_dir` resolves relative to the YAML file. Each invocation saves `<output_dir>/<suite-id>/status.jsonl` and `trials/<trial-id>/{assignment.json,container.log,output/}`. Launch failures also save `launch.log`.
- Exited containers are logged and removed; free slots refill independently. Individual failures do not stop the remaining trials.
- Ctrl+C or termination stops scheduling and cleans up this suite's containers/project resources while preserving output. Infrastructure failures stop scheduling and report cleanup failures if Docker is unreachable.
- Exit codes: `0` for all container exits successful, `1` for trial/infrastructure failures, `2` for invalid configuration, `130` for cancellation. Execution status does not establish verifier success.
- This version uses one Docker host, with no pair repetitions, retries, restart recovery, automatic resource sizing, or per-trial timeouts.

## Run a platform trial

Use `QaPlatformAdapter` to cap developer-driven episodes and confirm saved records before shutdown. Python owns the Unity Player process.

```text
qa-run-trial -- <Unity executable> [Player arguments]
  -> read QA_TRIAL_CONFIG and QA_OUTPUT_DIR -> open output and TCP listener
  -> start bootstrap -> load assigned scene -> require one enabled adapter
  -> validate Python readiness -> load CPU model -> validate tensor contract -> On Ready
  -> game calls StartUnit -> gameplay -> EndUnit -> append evidence to episodes.jsonl
  -> after X episodes: drain reports -> trial_complete
  -> Python flushes, fsyncs, and closes output -> trial_saved
  -> Unity exits 0 -> Python exits 0 -> container exits -> suite runner frees the slot
```

Install this package in the project image and use `qa-run-trial -- /app/Game -batchmode -nographics` as its main command. The suite runner supplies `QA_TRIAL_CONFIG` and `QA_OUTPUT_DIR`. For a local Player run, set those variables to an assignment JSON and a fresh output directory yourself. The assignment contains `suite_id`, `trial_id`, positive `episodes_per_trial`, and `environment`/`agent` objects with string `id` fields. Reusable production base images remain separate work; [the actor-control sample](examples/actor-control/README.md) supplies a small Linux build and image recipe for verification.

Derive your scene component from `Kcccr.QaFramework.QaPlatformAdapter`. Keep the `OnUnitStart()` / `OnUnitEnd()` hooks shown below and add the observation/action hooks:

```csharp
protected override int ObservationSize => 2;
protected override int ActionSize => 1;
protected override float[] CollectObservation() => new[] { actor.Position, 1f };
protected override void ApplyAction(float[] actions) => actor.Move(actions[0]);
```

Reference the game-specific actor on the concrete adapter. The base loads `agent.model` and owns a CPU worker; no `RlModelRunner` or separate policy driver is needed. Models must have exactly one static float input `[1, ObservationSize]` and one float output `[1, ActionSize]`. A zero-input startup pass validates output shape/type/finite values without applying actions. **On Ready** fires only after that pass and Python readiness. During active episodes, the base `FixedUpdate` collects observations, runs inference, and applies actions every **0.1 simulated seconds** by default (Inspector `Decision Interval`). `StartUnit` resets timing; ended/replaced episodes cannot receive stale outputs. Nonfinite values, shape mismatches, inference errors, and mapping exceptions fail the trial. Inference resources are disposed on completion, failure, or disablement.

Return a serializable `QaUnitReport` subclass with your typed `evidence`. Keep gameplay disabled until the adapter's **On Ready** event; call `StartUnit()` only when `CanStartUnit` is true, and call `EndUnit()` before resetting the environment. The game owns reset logic and episode end conditions. The adapter counts ended episodes, rejects overlapping/reentrant starts and starts after the limit, and ignores duplicate end calls. `CompletedEpisodes` counts queued reports; only the final acknowledgement confirms persistence.

Use one platform adapter for the whole trial; disabling or destroying it before completion fails the trial. Do not attach `QaRecorderLauncher` for this path. If overriding `Start`, `FixedUpdate`, `OnDisable`, or `OnApplicationQuit`, call the base implementation. The existing `QaUnitAdapter` + launcher path remains available for standalone consumers.

The generic collector validates schema version 2 and stores arbitrary JSON evidence with suite/trial/environment/agent IDs and a one-based episode index. Adding evidence fields needs no Python changes. It does not run predicates or model inference. Use the existing evaluator separately for developer-defined success rules.

Startup and final completion each have a 30-second timeout; gameplay has no duration limit. After acknowledgement, Unity has five seconds to exit. Failures or cancellation produce a nonzero runtime exit, stop/reap Unity, and preserve partial output. Existing `episodes.jsonl` files are rejected to avoid mixing trials. See [the platform protocol](docs/integration-readiness.md#platform-trials) for the wire and output formats.

See [the actor-control sample](examples/actor-control/README.md) for two environments and two embedded-weight policies that move the same actor in opposite directions without rebuilding the image. Linux verification uses Unity 6000.3.4f1 with Linux Build Support (Mono), a Linux x86-64 Player, CPU inference, and `-batchmode -nographics`. GPU inference, multi-input/recurrent models, and dungeon migration remain outside this iteration.

## Standalone per-game implementation

Derive a component from `Kcccr.QaFramework.QaUnitAdapter`, add it explicitly to your scene controller, and implement:

```csharp
protected abstract void OnUnitStart();
protected abstract QaUnitReport OnUnitEnd();
```

Use `OnUnitStart()` to initialize tracking and component references. In `OnUnitEnd()`, read your `IQaStateSource<TState>` components, aggregate their snapshots, and return a concrete serializable `QaUnitReport` subclass with a typed `evidence` field. The base fills `schema_version`, `unit_id`, and a fresh `attempt_id`, then calls inherited `SendReport()` automatically.

Call `StartUnit(unitId)` from your game's unit-start event and `EndUnit()` at its chosen end condition, before resetting or destroying the sources. End the active attempt before starting another. Repeated or reentrant `EndUnit()` calls do nothing after that attempt is consumed. Disabling/re-enabling the component preserves attempt state and never starts or ends a unit. Delivery runs asynchronously in report order to `127.0.0.1:8800` by default.

## Start the recorder from Unity

Add **QA Framework > QA Recorder Launcher** to an active scene GameObject. Set its Python executable, script arguments (or `-m your_module`), and working directory. Assign its **Adapter**, or keep the unit adapter on the same GameObject. The adapter supplies the TCP host/port and owns the connection. Keep the gameplay controller disabled in the saved scene; connect **On Ready** to that controller's `enabled = true` and **On Stopped** to `enabled = false` in the Inspector.

Your Python consumer runs its normal `QAReportReceiver.receive()` loop. The receiver automatically sends a newline-delimited readiness message when it accepts Unity's connection: `{"type":"ready","schema_version":2,"process_id":1234,"session_id":"..."}`. The launcher gives its child a fresh `QA_RECORDER_SESSION` environment value; the receiver echoes it as `session_id`. The launcher retries refused connections while Python starts, validates the schema and session, then invokes **On Ready**. That same connection carries subsequent unit reports. No readiness print statement is needed; stdout/stderr are ordinary Unity Console logs.

Disabling the launcher, unloading the scene, or stopping Play closes the connection and stops its owned Python process (including Windows virtual-environment child processes). Missing Python, an occupied port, incompatible readiness data, an unexpected session, early exit, or the startup timeout leaves gameplay paused. Python sends readiness once per accepted connection; unit reports flow Unity to Python, with no per-report acknowledgement or gameplay commands.

Use `{timestamp}` in the arguments for a fresh results filename per scene session. Relative working directories start at the Unity project root in the Editor, or beside the executable in desktop builds. The optional **Python Path** adds one source directory to `PYTHONPATH`, relative to the working directory; otherwise install this Python package into the chosen interpreter. Python must already be installed. Desktop builds also need the configured scripts/package beside the build or at configured external paths.

For the included printing example, set **Arguments** to `-u path/to/receive_unity_reports.py` using the path from the configured working directory. Use the selected interpreter's installed QA package, or set **Python Path** to its source directory. Your own consumer supplies requirements and persistence.

## Python

From this repository:

```shell
python -m pip install -e ".[test]"
python examples/receive_unity_reports.py --port 8800
```

The launcher can start this receiver for you; manual startup remains available. The receiving example prints validated reports without assuming any evidence fields. To produce QA results, supply your own named predicates:

```python
from qa_framework import QAReportReceiver, evaluate_unit

with QAReportReceiver(port=8800) as receiver:
    report = receiver.receive()
    result = evaluate_unit(report, {
        "target_activated": lambda unit: unit.evidence["activated"],
        "within_interaction_budget": lambda unit: unit.evidence["interaction_count"] <= 3,
    })
    print(result.to_dict())
```

`receive()` returns a `UnitReport` containing `unit_id`, `attempt_id`, and developer-defined evidence. Schema version 2 validates nonempty string identities and finite JSON objects. Evidence is copied and frozen so later mutations cannot change the received snapshot. Dictionaries/arrays are exported as ordinary JSON objects/arrays by `to_dict()`.

`evaluate_unit(report, requirements)` requires at least one named predicate. Each must return a boolean; overall `passed` is true exactly when all requirements pass. Completion, failure, seeds, recipes, player metrics, and ending reasons have no built-in QA meaning. Add them to your evidence and predicates when needed.

For an offline example and the existing checks:

```shell
python examples/evaluate_unit.py
python -m pytest
```

The old action/observation, level, and episode models are deprecated and retained in their source modules for reference. They are not exported by the package API or imported by the receiver/evaluator. Generic JSON and primitive validation are the only schema helpers loaded by the current runtime. The receiver/evaluator runtime uses only the Python standard library and Unity's built-in `JsonUtility`. The suite runner adds optional PyYAML parsing and invokes Docker Compose.

See [the integration guide](docs/integration-readiness.md) for a complete developer adapter example, wire format, and verification steps.

## Worked example

The sibling game's [QA integration guide](../kawaii-power-little-adventure/QA/README.md) demonstrates an adapter, state wrappers, lifecycle wiring, and an exit-time/success recorder. Its gameplay, evidence fields, predicates, and batch settings are integration choices.
