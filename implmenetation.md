# Implementation flows

Companion to [Platform refactor](platform-refactor.md). Single-host suite orchestration exists; model preparation/runtime binding and aggregation are planned. Existing lifecycle, TCP, and evaluation methods are named below.

## Platform Infrastructure

### Configuration / setup

```text
qa-run-suite suite.yaml
  -> validate image, positive episodes_per_trial and max_concurrent_trials,
     output_dir, nonempty environments/agents with unique string IDs
  -> check Docker engine and Compose availability
  -> generate unique suite ID and one Compose project
  -> lazily yield Y x K environment/agent assignments with distinct trial IDs
  -> write assignment JSON only when a slot is available

assignment.json: suite_id + trial_id + episodes_per_trial + environment + agent
  -> environment/agent settings pass through without interpretation

ONNX reference -> Unity Editor import -> ModelAsset
  -> ModelWriter.Save(path, modelAsset) -> .sentis artifact
  -> make the selected artifact available to its trial container
```

The single actor and its adapter are wired inside each scene. YAML carries scene and model references; the runner does not prepare models, load assets, or initialize Unity. Relative `output_dir` paths resolve beside the suite YAML.

Model preparation uses Unity's [model serialization](https://docs.unity3d.com/Packages/com.unity.ai.inference@2.2/api/Unity.InferenceEngine.ModelWriter.html).

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
- `QA_TRIAL_IMAGE`, `QA_TRIAL_CONFIG_HOST`, and `QA_OUTPUT_DIR_HOST` parameterize the single `trial` service.
- Each trial receives read-only `/qa/assignment.json` through `QA_TRIAL_CONFIG` and writable `/qa/output` through `QA_OUTPUT_DIR`.
- The image's existing entrypoint runs the trial; no entrypoint or command override is supplied.
- Save lifecycle records in `<output_dir>/<suite-id>/status.jsonl`; retain assignment, `container.log`, launch diagnostics, and isolated `output/` in `trials/<trial-id>/`.
- Individual launch failures and nonzero exits do not stop remaining trials; no automatic retries.
- CLI status is zero only if every trial container exits successfully; it does not report verifier success.
- Cancellation stops scheduling and removes only this invocation's containers/project resources; output remains on disk.
- An unavailable engine stops scheduling and records an infrastructure failure; cleanup failures are reported if Docker remains unreachable.
- One Docker host; no repeated pairs, restart recovery, resource sizing, or trial timeouts. A running container keeps its slot until exit or cancellation.

### Results and reporting

```text
Each Python consumer: evaluated episode + suite/trial/environment/agent context
  -> append to that trial's dedicated results file
  -> mounted output storage preserves files after containers stop
  -> platform collector reads Y x K trial files
  -> process each episode once
  -> aggregate per trial -> per agent across environments -> suite comparisons
```

- Show expected versus received episodes and verifier pass/fail counts.
- Calculate success rate from passed episodes / graded episodes.
- Aggregate episode records once; cumulative summaries are not additional episodes.
- Aggregator placement: → Add host process or separate container here.

## Trial

### Startup

```text
Image entrypoint reads QA_TRIAL_CONFIG and QA_OUTPUT_DIR
Trial inputs -> Unity Player -> selected scene -> single adapter -> actor reference
Model path -> ModelLoader.Load(path) -> initialize the actor's runner -> model ready
QaRecorderLauncher -> start Python -> TCP readiness handshake -> recorder ready
Model ready + recorder ready -> begin X-episode loop
```

- Runtime model selection is new work; the current runner loads an Inspector-assigned `ModelAsset`.
- The selected model must match the adapter's observation/action contract.
- Existing [QaRecorderLauncher](Unity/Runtime/QaRecorderLauncher.cs) checks receiver schema and launch session before releasing gameplay.
- Unity supports loading a serialized model with [ModelLoader.Load(path)](https://docs.unity3d.com/Packages/com.unity.ai.inference@2.2/api/Unity.InferenceEngine.ModelLoader.html).

→ Add the startup parameter names and single-adapter discovery interface here.

### Episode loop

```text
Initialize/reset environment for the episode
  -> game calls StartUnit(unit_id) -> fresh attempt GUID -> OnUnitStart()
  -> observe actor/environment -> encode inputs -> model inference
  -> decode outputs -> apply actor actions -> repeat until episode ends
  -> game calls EndUnit() -> OnUnitEnd() -> concrete QaUnitReport with evidence
  -> framework fills schema_version=2, unit_id, and attempt_id
  -> SendReport() -> JSON plus newline -> TCP -> Python
  -> QAReportReceiver.receive() -> UnitReport.from_dict()
  -> evaluate_unit(report, named boolean checks) -> EvaluationReport
  -> to_dict() -> attach trial context -> append episode result
  -> repeat for the next episode until X episodes are complete
```

- Existing [QaUnitAdapter](Unity/Runtime/QaUnitAdapter.cs) calls the developer's hooks; the game calls only `StartUnit()` and `EndUnit()`.
- The [receiver](src/qa_framework/receiver.py) validates incoming reports; the [evaluator](src/qa_framework/evaluation/runner.py) preserves evidence and records each check's result. All checks must pass for overall success.
- Evidence is collected at episode end. Observation encoding and action mapping remain game-specific.

### Completion

```text
X episode evaluations saved in the trial's file
  -> signal trial completion
  -> stop Unity and Python together
  -> platform collects the trial output for suite aggregation
```

`EndUnit()` queues asynchronous delivery; shutdown must wait for saved evaluation results. The current TCP protocol provides startup readiness, without per-report acknowledgements or gameplay commands.

→ Add trial-completion signaling and process shutdown details here.
