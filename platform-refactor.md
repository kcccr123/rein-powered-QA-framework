# Platform refactor

## Description

A modular Unity QA platform. Developers define tests and success rules; the platform runs trials and reports what happened.

The lifecycle hooks, evidence delivery, verifier execution, and bounded single-host container orchestration exist. Shared image packaging, generic collection/persistence, model preparation/runtime binding, and result aggregation are planned.

## Goal

- Run independent trials in parallel, including comparisons between agent/model versions.
- Let developers define test scenarios, state components, evidence, and success rules.
- Collect trial results, aggregate repeated trials, and report feedback.

## Platform Infrastructure

### Configuration / setup

The suite runner accepts the configuration below. Scene wiring and model preparation remain integration work.

#### Scene setup

Connect the scene's actor to model-driven gameplay and episode reporting so the platform can run and collect trials.

```text
Developer places one actor with its gameplay controller in the scene
  -> attach an environment adapter and reference the actor
  -> define observation encoding and action mapping
  -> expose actor identity and the observation/action contract for runtime model selection
  -> wire episode start/end hooks and game-specific evidence sources
  -> configure JSON report delivery to the shared Python collector over TCP
```

#### Image preparation

Package each Unity project as a runnable trial image while reusing the platform's Python collector and startup logic.

```text
Platform provides a base image with Python, the QA collector, and startup entrypoint
  -> developer builds Unity Player with scenes and QA adapters
  -> developer adds the Unity build and prepared models to the base image
  -> build and tag the project image
  -> reference the project image in suite YAML
```

#### YAML requirements

- `image`: the developer's project image, inheriting the shared startup entrypoint from the base image.
- `episodes_per_trial`: positive integer X.
- `max_concurrent_trials`: positive integer C, limiting active containers.
- `output_dir`: output root, resolved relative to the YAML file.
- `environments`: nonempty list of Y definitions with unique, nonempty string IDs.
- `agents`: nonempty list of K definitions with unique, nonempty string IDs.
- Environment and agent definitions pass through as JSON configuration, including scene/model references and any setup, seed, budget, or verifier settings.
- The runner does not prepare models, load assets, or initialize Unity.

#### End-to-end setup flow

1. The developer wires the actor, adapter, episode boundaries, and evidence sources in Unity.
2. The developer builds the Unity Player and prepares model artifacts for the image's target platform.
3. The developer builds a versioned project image from the platform base image, adding the Unity build, assets, and models.
4. The user references that image and its supported scenes/models in the suite YAML.
5. The user runs `qa-run-suite suite.yaml` after installing `qa-framework[platform]` and starting Docker.
6. The runner validates the complete YAML and checks Docker and Compose before scheduling.
7. The runner generates a unique suite ID and lazily assigns one distinct trial per environment/agent pair.
8. One Compose project launches fresh containers as slots become available, up to C at once.
9. Each container receives a read-only assignment JSON through `QA_TRIAL_CONFIG` and a dedicated writable directory through `QA_OUTPUT_DIR`.
10. The inherited entrypoint uses the assignment to start the Unity Player and shared Python collector; Unity owns scene/model initialization and the X-episode loop.
11. Unity sends episode evidence as JSON; the collector appends it with trial context without game-specific Python edits.
12. All X episode records must be saved before coordinated Unity/Python shutdown; completion signaling remains to be implemented.

The intended Unity integration imports ONNX models and exports `.sentis` artifacts before execution. The runner passes references through without preparing or mounting model artifacts itself.

### Execution

#### Run manager - runner.py

```text
RunManager(config)
       |
       v
    .run()
       |
       v
Initialize output + counters
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
- Uses the supplied image's entrypoint; the runner does not implement a trial executable.
- The intended game image contains one Unity Player instance with the selected agent and its Python evaluation consumer.
- Unity and Python communicate over localhost.
- Both processes need coordinated shutdown. Docker supports [multiple processes in one container](https://docs.docker.com/engine/containers/multi-service_container/).

#### Container launching

- Use the packaged `src/qa_framework/platform/compose.yaml` with one parameterized `trial` service.
- One Compose project per test suite.
- Launch each trial with `docker compose -p <suite-id> ... run --detach --no-deps --name <trial-id> trial` and track the returned container ID.
- Mount the assignment read-only at `/qa/assignment.json`, exposed as `QA_TRIAL_CONFIG`.
- Mount the trial's writable `output/` at `/qa/output`, exposed as `QA_OUTPUT_DIR`.
- Preserve `container.log`, launch-failure diagnostics, and `output/` under `<suite-id>/trials/<trial-id>/`.

### Results and reporting

#### Result collection and storage

- Each trial container's Python consumer appends its X episode results to one file dedicated to that trial.
- Records include episode evidence, verifier results, and suite, trial, environment, agent/model, and episode attempt identifiers.
- Files live in mounted output storage accessible to the platform after trial containers stop.

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
    platform/                  YAML CLI, bounded run manager, Compose template
```

### Workflow

```text
             Configuration / setup
                       |
                       v
                 Run manager
                       |
                       v
            Docker Compose project
                (one per suite)
                 /    |    \
                v     v     v
             Trial 1 Trial 2 Trial 3   ... up to C active; refill on exit
                |     |     |
             Prepare independent environments
             Run agents
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

Each trial runs its X episodes inside one Docker container containing one Unity Player instance and its Python evaluation consumer. One Docker Compose project groups the suite's trial containers.

### Existing framework components

#### Unity

- `QaUnitAdapter`: start/end hooks, attempt IDs, and report delivery.
- `IQaStateSource<T>`: interface for developer-owned state snapshots.
- `QaUnitReport`: shared identity and developer-defined evidence.
- `QaRecorderLauncher`: starts Python and checks readiness before enabling gameplay.

#### Python

- `QAReportReceiver`: receives and validates Unity reports.
- `UnitReport`: unit ID, attempt ID, and evidence.
- `evaluate_unit()`: runs named boolean verifiers; all must pass.
- `EvaluationReport`: evidence, individual verifier results, and overall pass/fail.

### Developer-defined components

- Scenario setup, initial state, and reset behavior.
- Agent implementation and environment controls.
- State-source components and the adapter that combines their evidence.
- Test start/end conditions and execution budgets.
- Named verifiers defining acceptable behavior.

### Trial lifecycle

Within a trial, the game calls `StartUnit()` and `EndUnit()` once per episode. The adapter's `OnUnitStart()` hook initializes episode tracking; `OnUnitEnd()` gathers that episode's final evidence for Python evaluation. The framework does no continuous state recording.

```text
Run manager mounts assignment JSON + dedicated output directory
                            |
                            v
+--------------------------------------------------------+
| ONE TRIAL CONTAINER                                    |
|                                                        |
|  Built Unity game + selected agent                     |
|          |                                             |
|          v                                             |
|  StartUnit -> run test -> EndUnit                      |
|                              |                         |
|                              v                         |
|                   Evidence sent to Python              |
|                              |                         |
|                              v                         |
|                    Run custom verifiers                |
|                              |                         |
|                              v                         |
|                   Save result + evidence               |
+--------------------------------------------------------+
                            |
                            v
              Platform collects the result
```

The trial must save the evaluation results for all X episodes before shutdown. `EndUnit()` currently queues asynchronous delivery.

### QaUnitAdapter flow

- **Actor control:** host the selected model and drive the actor through observation -> inference -> action.
- **External communication and data aggregation:** combine actor/environment state into episode evidence and manage communication with Python. Python evaluates and stores results; the platform aggregates across trials.

Target flow. Model/actor integration and shared trial saving/completion are planned; episode hooks and TCP reporting already exist.

```text
Startup (once per trial):
Scene + trial configuration -> adapter references actor + initializes selected model
  -> QaRecorderLauncher starts Python -> adapter.ConnectAsync()
  -> TCP readiness handshake -> validate schema + launch session -> ready

Each episode:
Game sets up/resets environment -> StartUnit(unitId) -> new attempt ID -> OnUnitStart()
  -> observe -> encode -> model inference -> decode -> apply actor action (repeat)
  -> game calls EndUnit() -> OnUnitEnd() collects final evidence -> QaUnitReport
  -> JSON + newline -> queued TCP delivery -> Python verifiers -> trial results file
  -> next episode, until X episodes are complete

Completion:
All X evaluations saved -> coordinated Unity/Python shutdown -> platform collects results
```

`EndUnit()` queues delivery; it does not confirm that Python saved the result. Completion signaling is still undefined.

### Alternative: file export

- Flow: Unity writes episode evidence to a file; the platform consumes it instead of receiving it over TCP.
- Pro: no TCP connection state to manage.
- Con: file writes and reads add I/O and can increase latency.
- Con: distributed runs need an additional file-transfer or ingestion step.
- Con: file-writing and output-format maintenance become Unity's responsibility.
- Preference: Unity exports evidence; the platform handles analysis and report generation for ease of use.
