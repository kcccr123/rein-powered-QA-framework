# Platform refactor

## Description

A modular Unity QA platform. Developers define tests and success rules; the platform runs trials and reports what happened.

The lifecycle hooks, generic evidence storage, confirmed trial shutdown, standalone verifier execution, bounded single-host orchestration, automated ONNX conversion, read-only model mounting, scene selection, and adapter-owned CPU inference exist. The [actor-control sample](examples/actor-control/README.md) supplies a runnable Linux image recipe. Reusable production base images and result aggregation remain planned.

## Goal

- Run independent trials in parallel, including comparisons between agent/model versions.
- Let developers define test scenarios, state components, evidence, and success rules.
- Collect trial results, aggregate repeated trials, and report feedback.

## Platform Infrastructure

### Configuration / setup

The suite runner accepts the configuration below. `QaPlatformAdapter` and `qa-run-trial` implement episode counting, collection, and shutdown. Developers wire the actor, observation encoding, action mapping, and episode/evidence hooks. The platform prepares and mounts models, selects scenes, and drives CPU decisions.

#### Scene setup

Connect the scene's actor to model-driven gameplay and episode reporting so the platform can run and collect trials.

```text
Developer places one actor with its gameplay controller in the scene
  -> derive QaPlatformAdapter; reference actor and evidence sources
  -> declare ObservationSize/ActionSize; implement CollectObservation/ApplyAction
  -> wire On Ready to enable gameplay after Python and model readiness
  -> game calls StartUnit and EndUnit around each episode
  -> OnUnitEnd returns typed evidence -> adapter counts episodes and sends JSON
  -> base FixedUpdate owns observation -> CPU inference -> action every 0.1 simulated seconds
```

#### Image preparation

Package each Unity project as a runnable trial image. Supply models separately so changing a compatible model does not require rebuilding the image.

The sample uses Linux Build Support (Mono) in Unity 6000.3.4f1, a Linux x86-64 Player,
CPU inference, and `-batchmode -nographics`. Its image sets `SDL_VIDEODRIVER=dummy`
for native input initialization without a display. Windows build support is retained.

```text
Planned base image includes Python, the QA package, and qa-run-trial
  -> developer builds Unity Player with bootstrap first, environments, and concrete QA adapter
  -> developer adds the Unity build and scene assets to the base image
  -> main command: qa-run-trial -- <Unity executable> [Player arguments]
  -> build and tag the project image
  -> reference the project image in suite YAML
```

#### Automated model conversion

Convert each ONNX model once before scheduling trials. The conversion machine needs Unity Editor; no Inspector interaction is required.

```text
User supplies an ONNX file through agents[].model in suite YAML
  -> automated Unity Editor job runs with -batchmode -executeMethod <conversion script>
  -> isolated temporary project pins Inference Engine 2.4.1 (same as runtime)
  -> reject malformed ONNX and external weights; import each resolved path once per suite
  -> ModelWriter.Save(...) exports a .sentis file under <suite-id>/models
  -> preserve manifest + preparation/unity logs; remove temporary project after Editor exits
  -> fail or cancel before trials on conversion error, timeout, or cancellation
```

#### Model mounting and runtime loading

Reuse the prepared model across its environment trials. Each container uses the compiled Player and initializes its own runtime model from the shared read-only artifact.

```text
Prepared .sentis file on the host
  -> runner mounts the selected file read-only at /qa/model/agent.sentis
  -> trial assignment sets agent.model to /qa/model/agent.sentis
  -> Unity calls ModelLoader.Load(path) and initializes its own inference worker
  -> QaPlatformBootstrap selects environment.scene and requires one enabled adapter
  -> adapter validates one static float [1, ObservationSize] input and [1, ActionSize] output
  -> zero-input startup inference validates compatibility without applying actions
  -> model ready + Python ready -> begin episodes
```

#### YAML requirements

- `image`: the developer's prepared project image; the reusable base image is planned.
- `episodes_per_trial`: positive integer X.
- `max_concurrent_trials`: positive integer C, limiting active containers.
- `output_dir`: output root, resolved relative to the YAML file.
- `environments`: nonempty list of Y definitions with unique, nonempty string IDs.
- `agents`: nonempty list of K definitions with unique, nonempty string IDs.
- `model_preparation.unity_editor`: existing host Editor executable; optional positive `timeout_seconds` defaults to 600. Editor/model/output relative paths resolve beside the YAML.
- `agents[].model`: existing self-contained ONNX path; the generated assignment uses the mounted `.sentis` path.
- `environments[].scene`: full scene path or unique scene name included in the Player build.
- Other environment and agent settings pass through as JSON configuration, including scene references and any setup, seed, budget, or verifier settings.

#### End-to-end setup flow

1. The developer wires the actor, adapter, episode boundaries, and evidence sources in Unity.
2. The developer builds the Unity Player for the image's target platform, including its bootstrap, environment scenes, and concrete adapter's observation/action mapping.
3. The developer builds a versioned project image containing the QA package, Unity build, and scene assets; a reusable base image is planned.
4. The user references that image, its supported scenes, and host ONNX files in the suite YAML.
5. The user runs `qa-run-suite suite.yaml` after installing `qa-framework[platform]` and starting Docker.
6. The runner validates YAML and prepares distinct models before checking Docker/Compose and scheduling trials.
7. The runner generates a unique suite ID and lazily assigns one distinct trial per environment/agent pair.
8. One Compose project launches fresh containers as slots become available, up to C at once.
9. Each container receives a read-only assignment JSON through `QA_TRIAL_CONFIG` and a dedicated writable directory through `QA_OUTPUT_DIR`. The model mount exposes the selected `.sentis` file read-only, with its container path in the assignment.
10. The image runs `qa-run-trial -- <Unity executable> [args]`; Python opens its listener and starts Unity. The bootstrap selects the scene and the adapter loads the mounted artifact, gating gameplay on model and Python readiness. `QaPlatformAdapter` counts the game's start/end calls up to X episodes.
11. Unity sends episode evidence as JSON; the collector appends it with trial context without game-specific Python edits.
12. After X episodes, Unity requests completion; Python flushes, fsyncs, and closes output, then acknowledges. Unity exits, Python exits, and the runner collects the container status.

Conversion happens once per model in the preparation stage; runtime loading and worker initialization happen independently in each trial container.

### Execution

#### Run manager - runner.py

The preparation stage runs before this scheduling loop and supplies prepared model paths for assignments and mounts. No cross-suite cache is used.

```text
RunManager(config)
       |
       v
    .run()
       |
       v
Initialize output + counters
Prepare distinct models in isolated Editor; preserve artifacts/logs
Check Docker + create assignment iterator
       |
       v
+-----------------------------------------------------+
| RUN LOOP: assignments left or trials still active    |<--+
|                                                     |   |
| Fill free slots up to max_concurrent_trials          |   |
|   -> next environment/agent pair                     |   |
|   -> save assignment -> launch trial container       |   |
|                                                     |   |
| Active trials run in parallel                       |   |
|   -> wait 1 second -> inspect active containers      |   |
|   -> finished: save logs -> remove -> free slots     |   |
+-----------------------------------------------------+   |
       |                                                 |
       +-- assignments left or active trials ------------+
       |
       v  queue empty + no active trials
Cleanup suite resources -> save final status -> return exit code
```

- Read suite YAML through the optional `platform` dependency and manage one Docker Compose project per invocation.
- Generate assignments lazily; keep at most C active containers without precreating containers or scheduling tasks.
- Save `assignment.json` with suite/trial IDs, episode count, and the selected environment/agent definitions.
- Model handling: select the prepared artifact for the agent, mount it read-only, and write its container path into the assignment.
- Append lifecycle records to `<output_dir>/<suite-id>/status.jsonl`.
- Inspect all active containers together once per second; save exit codes and logs, remove exited containers, and refill available slots.
- Continue after individual launch failures and nonzero exits, without retries.
- Return success only when every trial container exits with code zero; this records execution status rather than verifier success.
- Stop scheduling on cancellation or Docker infrastructure failure; clean up this invocation's containers and project resources while preserving output.

#### Parallel execution

- Run independent trials concurrently within the suite's Compose project, with separate state and output locations.
- Each container occupies a slot until it exits or the suite is cancelled; different durations refill slots independently.
- This version uses one Docker host, with no pair repetitions, retries, restart recovery, automatic resource sizing, or per-trial timeouts.

#### Trial containerization

- One Docker container per trial.
- Uses the supplied image's entrypoint, configured to run `qa-run-trial` as the main process.
- Python owns one Unity Player child and receives reports over localhost.
- Python confirms saved output before Unity exits; Python then returns the trial's exit status.
- Runtime failure or cancellation stops and reaps Unity; partial output remains available.

#### Container launching

- Use the packaged `src/qa_framework/platform/compose.yaml` with one parameterized `trial` service.
- One Compose project per test suite.
- Launch each trial with `docker compose -p <suite-id> ... run --detach --no-deps --name <trial-id> trial` and track the returned container ID.
- Mount the assignment read-only at `/qa/assignment.json`, exposed as `QA_TRIAL_CONFIG`.
- Bind-mount the selected host `.sentis` file read-only at `/qa/model/agent.sentis`; set `agent.model` in the assignment to that container path.
- Mount the trial's writable `output/` at `/qa/output`, exposed as `QA_OUTPUT_DIR`.
- Preserve `container.log`, launch-failure diagnostics, and `output/` under `<suite-id>/trials/<trial-id>/`.

### Results and reporting

#### Result collection and storage

The generic collector preserves evidence without game-specific Python classes. Verifier execution remains separate and requires developer-supplied predicates.

```text
Schema-2 report -> validate envelope and unique attempt ID
  -> attach suite, trial, environment, agent, and one-based episode index
  -> append to QA_OUTPUT_DIR/episodes.jsonl -> flush each record
  -> final completion request -> fsync and close -> acknowledge saved output
  -> mounted files remain available after container exit
```

#### Aggregation and comparison

- The platform aggregator reads the suite's Y x K trial files and processes each episode result once.
- Aggregate results per trial, per agent across environments, and across the suite.
- Compare agents on the same environments; show expected versus received episode counts.
- Whether the aggregator runs on the host or in its own container is undecided.

### Tooling

Platform plugins that extract additional information from the environment or affect the environment.

### Folder structure

Current package locations:

```text
Unity/Runtime/                 Unity integration components
src/qa_framework/
    receiver.py                Python report receiver
    evaluation/                Report types and verifier execution
    platform/                  Suite runner, Compose template, trial process/collector
```

### Workflow

```text
             Configuration / setup
                       |
                       v
             Automated ONNX conversion
             Prepared .sentis files on host
                       |
                       v
                 Run manager
                       |
                       v
            Docker Compose project
                (one per suite)
             Read-only model mounts
                 /    |    \
                v     v     v
             Trial 1 Trial 2 Trial 3   ... up to C active; refill on exit
                |     |     |
             Prepare independent environments
             Load assigned model + initialize CPU worker
             Adapter FixedUpdate drives active episode decisions
             Capture evidence
             Run verifiers
                |     |     |
                 \    |    /
                       v
             Store results and traces
                       |
                       v
             Aggregate trial results
                       |
                       v
             Reports and comparisons
```

## Trials

### Definition

- Environment: one Unity scene with its setup/reset configuration. Different levels generated within that scene across episodes still belong to the same environment.
- Episode: the most basic unit, one attempt in an environment. The game calls `StartUnit()` once at the start and `EndUnit()` once at the end.
- Trial (simulation): runs X episodes for one environment and one agent.
- Suite: runs each of K agents in each of Y environments, giving Y trials per agent, Y x K trial runs, and X x Y x K episodes.

Each trial runs its X episodes inside one Docker container containing the Python runtime and its Unity Player child. One Docker Compose project groups the suite's trial containers.

### Existing framework components

#### Unity

- `QaPlatformBootstrap`: selects the assigned build scene; rejects missing/ambiguous scenes and missing/multiple enabled adapters.
- `QaPlatformAdapter`: CPU model loading, vector validation, timed decisions, start/end hooks, attempt IDs, episode limit, ordered delivery, and saved-output shutdown.
- `QaUnitAdapter`: standalone start/end hooks, attempt IDs, and report delivery.
- `IQaStateSource<T>`: interface for developer-owned state snapshots.
- `QaUnitReport`: shared identity and developer-defined evidence.
- `QaRecorderLauncher`: starts Python for the standalone adapter; platform trials use Python as the parent.

#### Python

- `qa-run-trial`: starts Unity, stores generic episode evidence, acknowledges saved output, and cleans up the child.
- `QAReportReceiver`: receives and validates reports for standalone consumers.
- `UnitReport`: unit ID, attempt ID, and evidence.
- `evaluate_unit()`: runs named boolean verifiers; all must pass.
- `EvaluationReport`: evidence, individual verifier results, and overall pass/fail.

### Developer-defined components

- Scenario setup, initial state, and reset behavior.
- Actor mechanics, observation encoding, action mapping, and declared vector sizes.
- State-source components and the adapter that combines their evidence.
- Test start/end conditions and execution budgets.
- Named verifiers defining acceptable behavior.

### Trial lifecycle

The game owns episode setup and end conditions. `QaPlatformAdapter` counts completed episodes and owns the completion handshake; Python owns the Player process. The adapter owns inference and timing; the developer owns episode boundaries and evidence.

```text
Run manager mounts assignment JSON + output directory
  -> mount selected prepared model read-only
  -> qa-run-trial opens output and TCP listener -> starts Unity
  -> bootstrap reads assignment -> selects scene -> requires one enabled adapter
  -> adapter validates Python readiness -> loads model -> CPU validation pass
  -> model ready + Python ready -> On Ready enables gameplay
  -> game resets environment -> StartUnit -> OnUnitStart
  -> FixedUpdate collects observation -> inference -> ApplyAction while episode active
  -> game calls EndUnit -> OnUnitEnd gathers final evidence
  -> adapter fills report identity -> queues JSON -> Python appends episode
  -> repeat while CanStartUnit, until X episodes have ended
  -> stop accepting new episodes -> drain reports -> send trial_complete
  -> Python verifies X distinct attempts -> flush + fsync + close episodes.jsonl
  -> send trial_saved -> Unity exits 0 -> Python exits 0 -> container exits
  -> run manager saves logs/status -> removes container -> refills slot
```

### QaPlatformAdapter flow

The adapter keeps the existing lifecycle hooks and evidence format and adds observation/action hooks. It accepts one static float input `[1, ObservationSize]` and one float output `[1, ActionSize]`. Startup inference applies no actions. Decisions run every configurable 0.1 simulated seconds during active episodes only; StartUnit resets timing and stale outputs cannot cross episode boundaries. Model/inference/mapping errors fail the trial. Inference resources are disposed on completion, failure, and disablement. Use one adapter throughout the trial; `QaUnitAdapter` and its launcher remain available for standalone use.

```text
Before readiness / active episode / end hook / completion -> reject StartUnit
Repeated or reentrant EndUnit -> no additional report
Disable adapter, invalid report, disconnect, or save failure -> fail trial -> stop Unity
Cancellation -> Python stops and reaps Unity -> nonzero exit -> retain partial output
```

Startup and final send/acknowledgement each allow 30 seconds. After the final report, Python allows 30 seconds for the completion request; after acknowledgement, Unity has five seconds to exit. Cleanup terminates the child, waits two seconds, then kills and reaps it if necessary. Gameplay itself has no duration timeout. See [the integration contract](docs/integration-readiness.md#platform-trials) for message and record formats.

GPU inference, general multi-input/recurrent policies, dungeon migration, and reusable production base images remain outside this iteration.

### Alternative: file export

- Flow: Unity writes episode evidence to a file; the platform consumes it instead of receiving it over TCP.
- Pro: no TCP connection state to manage.
- Con: file writes and reads add I/O and can increase latency.
- Con: distributed runs need an additional file-transfer or ingestion step.
- Con: file-writing and output-format maintenance become Unity's responsibility.
- Preference: Unity exports evidence; the platform handles analysis and report generation for ease of use.
