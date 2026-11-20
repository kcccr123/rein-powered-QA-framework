# Implementation flows

Companion to [Platform refactor](platform-refactor.md). Single-host suite orchestration, the platform adapter, generic collection, and confirmed shutdown exist. Managed model preparation, scene selection, read-only mounting, and adapter-owned CPU inference are implemented. The sample provides a Linux image recipe; reusable production images and aggregation remain planned.

## Platform Infrastructure

### Configuration / setup

```text
qa-run-suite suite.yaml
  -> validate image, positive episodes_per_trial and max_concurrent_trials,
     output_dir, nonempty environments/agents with unique string IDs
  -> require model_preparation.unity_editor and positive timeout_seconds (default 600)
  -> resolve Editor/model/output host paths beside YAML
  -> require self-contained ONNX files and environment.scene
  -> prepare each distinct model path once; abort before trials on failure/timeout/cancellation
  -> check Docker engine and Compose availability
  -> generate unique suite ID and one Compose project
  -> lazily yield Y x K environment/agent assignments with distinct trial IDs
  -> write assignment JSON only when a slot is available

assignment.json: suite_id + trial_id + episodes_per_trial + environment + agent
  -> copy agent settings with model=/qa/model/agent.sentis; preserve source config
  -> retain other environment/agent JSON settings

ONNX file -> reject external weights -> isolated temporary Unity project
  -> pin Inference Engine 2.4.1 -> batchmode import -> ModelAsset
  -> ModelLoader.Load -> ModelWriter.Save -> suite models/*.sentis
  -> preserve manifest + preparation.log + unity.log; remove temp project after process exits
  -> mount selected artifact read-only for each trial; no cross-suite cache
```

The developer wires an actor reference, observation encoding, action mapping, evidence, and episode boundaries in each concrete adapter. Build a QaPlatformBootstrap scene first. The runner prepares models; the bootstrap selects the assigned scene; the adapter loads and drives the CPU policy. See the [sample build and suite](examples/actor-control/README.md).

Model preparation uses Unity's [model serialization](https://docs.unity3d.com/Packages/com.unity.ai.inference@2.4/api/Unity.InferenceEngine.ModelWriter.html).

### Execution

```text
Lazy assignments + C available slots
  -> supply image and host mount paths to packaged platform/compose.yaml
  -> docker compose -p <suite-id> ... run --detach --no-deps --name <trial-id> trial
  -> retain returned container ID; at most C active containers
  -> inspect active containers together once per second
  -> on exit: save exit code + logs -> remove container -> refill slot
  -> continue until assignments are exhausted and all active containers exit
```

- Install `qa-framework[platform]` for YAML parsing; Docker and Compose must be available.
- `QA_TRIAL_IMAGE`, `QA_TRIAL_CONFIG_HOST`, `QA_OUTPUT_DIR_HOST`, and `QA_MODEL_HOST` parameterize the single `trial` service.
- Each trial receives read-only `/qa/model/agent.sentis` and `/qa/assignment.json` through `QA_TRIAL_CONFIG` and writable `/qa/output` through `QA_OUTPUT_DIR`.
- The image's existing entrypoint runs the trial; no entrypoint or command override is supplied.
- Save lifecycle records in `<output_dir>/<suite-id>/status.jsonl`; retain assignment, `container.log`, launch diagnostics, and isolated `output/` in `trials/<trial-id>/`.
- Individual launch failures and nonzero exits do not stop remaining trials; no automatic retries.
- CLI status is zero only if every trial container exits successfully; it does not report verifier success.
- Cancellation stops scheduling and removes only this invocation's containers/project resources; output remains on disk.
- An unavailable engine stops scheduling and records an infrastructure failure; cleanup failures are reported if Docker remains unreachable.
- One Docker host; no repeated pairs, restart recovery, resource sizing, or trial timeouts. A running container keeps its slot until exit or cancellation.

### Results and reporting

```text
qa-run-trial: validated report + suite/trial/environment/agent context + episode index
  -> append to QA_OUTPUT_DIR/episodes.jsonl
  -> developer-supplied predicates can evaluate the stored reports separately
  -> mounted output storage preserves files after containers stop
  -> platform collector reads Y x K trial files
  -> process each episode once
  -> aggregate per trial -> per agent across environments -> suite comparisons
```

Aggregation below is planned; the current collector stores evidence without running verifiers.

- Show expected versus received episodes and verifier pass/fail counts.
- Calculate success rate from passed episodes / graded episodes.
- Aggregate episode records once; cumulative summaries are not additional episodes.
- Aggregator placement: → Add host process or separate container here.

## Trial

### Startup

Python owns the Player process. The image supplies a Linux x86-64 Player with bootstrap and gameplay scenes, using CPU inference and -batchmode -nographics.

Install Linux Build Support (Mono) through Hub for Unity 6000.3.4f1; retain Windows
support. The sample image sets `SDL_VIDEODRIVER=dummy` so headless native input
initializes successfully and excludes App UI runtime initialization.

```text
Image main command: qa-run-trial -- <Unity executable> [Player arguments]
  -> read QA_TRIAL_CONFIG -> validate identity and episode count
  -> create QA_OUTPUT_DIR/episodes.jsonl exclusively -> bind loopback TCP listener
  -> start Unity with QA_REPORT_PORT and a fresh QA_RECORDER_SESSION
  -> QaPlatformBootstrap reads assignment -> loads environment.scene
  -> missing/ambiguous scene or not exactly one enabled adapter fails startup
  -> QaPlatformAdapter connects -> validates readiness
  -> check schema 2, platform protocol 1, session, suite/trial IDs, and episode count
  -> load mounted model -> create CPU worker -> zero-input startup inference
  -> require one static float [1, ObservationSize] input and float [1, ActionSize] output
  -> On Ready enables gameplay only after model and Python readiness
```

Startup has a 30-second deadline. Platform scenes use `QaPlatformAdapter`, without `QaRecorderLauncher`. The converter and runtime both pin Inference Engine 2.4.1.

### Episode loop

The developer supplies actor mechanics and episode boundaries. The adapter owns its FixedUpdate decision loop and episode limit; no separate policy-driving component is required.

```text
CanStartUnit -> game initializes/resets environment -> StartUnit(unit_id)
  -> fresh attempt GUID -> reset decision timer -> OnUnitStart()
  -> every configurable 0.1 simulated seconds while episode active:
     CollectObservation() -> CPU inference -> ApplyAction(float[] actions)
  -> discard outputs if the episode ended or was replaced; validate finite vector values
  -> game calls EndUnit() -> latch episode ended -> OnUnitEnd()
  -> concrete QaUnitReport with typed evidence
  -> fill schema_version=2, unit_id, attempt_id -> queue JSON line over TCP
  -> Python UnitReport.from_dict() -> reject duplicate/excess reports
  -> attach suite/trial/environment/agent IDs and one-based episode_index
  -> append and flush episodes.jsonl -> next episode if below X
```

The adapter rejects starts during an active episode, during the end hook, and after the final episode. Repeated/reentrant ends do nothing. `CompletedEpisodes` counts queued reports, not confirmed persistence. Evidence is collected at episode end; Python preserves arbitrary finite JSON objects. The existing `evaluate_unit()` remains available separately for developer-defined boolean predicates.

### Completion

Unity exits successfully only after Python confirms the complete output file is saved.

```text
Final EndUnit -> block further starts -> drain pending reports
  -> send trial_complete(suite_id, trial_id, completed_episodes)
  -> Python checks matching identity and exactly X distinct attempts
  -> flush -> fsync -> close episodes.jsonl
  -> send trial_saved(suite_id, trial_id, saved_episodes)
  -> Unity validates acknowledgement -> dispose worker -> Application.Quit(0)
  -> Python waits for zero exit -> returns 0 -> container exits
  -> suite runner saves logs/status -> removes container -> frees slot
```

Final sending/acknowledgement has a 30-second deadline. Python allows 30 seconds after the final report for `trial_complete`, then five seconds after acknowledgement for Unity to exit. Missing/invalid completion, save errors, disconnection, and early/nonzero Player exits fail the runtime. On failure or cancellation, Python terminates Unity, waits two seconds, then kills and reaps it if needed. Partial output remains on disk. There is no gameplay duration timeout.

Model loading, tensor compatibility, inference, and observation/action mapping failures use the trial failure path. Completion, failure, and disablement dispose inference resources. Cancellation during preparation stops/reaps the owned conversion Editor before removing its temporary project; artifacts and logs remain in suite output. GPU inference, multi-input/recurrent models, dungeon migration, and reusable production base images are outside this iteration.
