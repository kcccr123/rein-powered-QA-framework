# Platform refactor

## Description

A modular Unity training and QA platform. Developers define training environments, rewards, tests and success rules; a DAG connects curriculum stages and experimental branches to evaluation suites.

The platform runs containerized CPU PPO training through the existing Unity reinforcement learning library, resumes full checkpoints, exports ONNX policies, converts them for Unity inference, and runs bounded parallel trials. It saves lineage, execution status and evidence. Grading, result aggregation and the final comparison report remain planned.

See [the DAG guide](docs/training-dag.md) for configuration and runtime contracts and [the actor sample](examples/actor-control/README.md) for runnable images.

## Configuration and execution

- Each YAML node has an ID, image and type: training or test suite.
- Training inputs are checkpoint paths or another training node's checkpoint output. Branches load separate copies of the same parent state and apply their own scene parameters.
- Suite agents use host ONNX files or training-node ONNX outputs. A suite runs its environment/agent matrix using the existing trial runtime.
- Artifact references define dependencies. Fixed-budget training completes before dependent work starts; there are no metric gates or model merges.
- All containers share one limit and one Compose project. Suite nodes expand lazily into trials and consume no extra orchestration container.
- Training produces a checkpoint, ONNX, resolved trainer settings and a manifest. Parent inputs are mounted read-only; outputs are isolated per node.
- ONNX conversion runs on the host when evaluation needs an available artifact, once per resolved source path per run. Runtime inference remains CPU-only with Inference Engine 2.4.1.
- Failure blocks dependent work while independent branches continue. Individual failed trials do not stop their suite's remaining trials. Cancellation and infrastructure failure stop scheduling and preserve partial files during cleanup.

```text
Starting checkpoint (or fresh policy)
              |
       Walking training
          /       \
 Running training  Walking variant
          \       /
           Test suite
      environment x agent trials
              |
      Saved episode evidence
              |
      Planned suite aggregation
              |
      Planned combined report
```

### Run manager

- Ready training stages and suite trials share the same container limit.
- The run loop preserves dependency ordering and continues independent branches after a node fails.

```text
RunManager(config)
       |
       v
    .run()
       |
       v
Initialize DAG node states + output directories
Check Docker + create the shared ready queue
       |
       v
+-----------------------------------------------------+
| RUN LOOP: pending nodes or work still running       |<--+
|                                                     |   |
| Resolve dependencies                                |   |
|   -> successful parents: make node ready            |   |
|   -> failed parents: block dependent node           |   |
|   -> suite ready: prepare available ONNX models     |   |
|                                                     |   |
| Fill free slots up to max_concurrent_containers     |   |
|   -> next ready training job or suite trial         |   |
|   -> save assignment -> launch container            |   |
|                                                     |   |
| Active training and trials run in parallel          |   |
|   -> wait 1 second -> inspect active containers     |   |
|   -> finished: save logs -> remove -> free slots    |   |
|   -> validate outputs -> update node status         |   |
+-----------------------------------------------------+   |
       |                                                  |
       +-- pending nodes or running work -----------------+
       |
       v  all nodes terminal + no active containers
Cleanup run resources -> save final manifest -> return exit code
```

### Developer integration

Training Players start in a QaTrainingBootstrap scene. Each training scene has a developer QaTrainingAdapter that applies assignment parameters, starts the existing RlTrainingHost, and returns its port. The Python entrypoint waits for a readiness file, calls the existing trainer/exporter, then stops and reaps its Player. See the DAG guide for the complete contract.

Evaluation keeps QaPlatformBootstrap and QaPlatformAdapter: select a scene, load the mounted policy, validate the vector contract, count developer-owned episodes, and confirm saved evidence before shutdown. Training and evaluation must share observation/action shapes and meanings.

### Output and reporting

Each invocation saves a run manifest and status log, per-node configuration/dependency manifests, per-job assignments and logs, and the existing episode JSONL files. Training artifacts retain parent references. A final run manifest includes succeeded, failed, blocked and cancelled nodes; it is an execution index, not a graded report.

The future aggregator will read suite trial files, grade each episode once, aggregate per trial/environment/agent/suite, and combine comparable suites into a final report after the DAG finishes. It must retain missing-episode and execution-failure information separately from failed QA requirements. Its execution location remains undecided.

Developers can define metrics around anything they can observe and clearly judge in the game. Some describe each episode; others summarize many episodes.

| Metric | Example definition | What it tells us |
| --- | --- | --- |
| Objective completion | Did the actor reach the exit with the required item? | Whether the intended task was completed |
| Completion time | Simulated seconds taken to finish | How efficiently the agent completes the task |
| Rule violations | Number of times the actor crossed a locked door or left valid boundaries | Whether gameplay rules were broken |
| Interaction correctness | Did activating a switch produce the expected door-state change? | Whether mechanics behave as specified |
| Coverage | Percentage of reachable rooms visited or mechanics exercised | How much of the game was actually tested |
| Stuck duration | Time spent attempting movement without meaningful progress | Possible navigation or collision problems |
| Resource outcomes | Damage taken, ammunition spent, items collected | Whether gameplay meets task-specific expectations |
| Game stability | Crashes, exceptions, corrupted state | Whether testing exposed runtime defects |

## Training

### Definition

- Training environment: one Unity scene with its setup/reset configuration, observation/action contract, reward function, and episode-ending rules. Node parameters can vary its difficulty, movement constraints, or other developer-defined settings.
- Training episode: one attempt from an environment reset until the environment reports completion. The trainer resets the environment for the next attempt.
- Training step: one action followed by its resulting observation, reward, and completion flag. An action may span multiple Unity frames.
- Training stage: one `train` node that learns in one configured environment for a timestep budget, starting from a fresh policy or a compatible checkpoint.
- Curriculum: a sequence of stages, each continuing the checkpoint produced by its predecessor.
- Training branch: an independent continuation of a parent checkpoint with its own environment parameters and stage settings. Multiple children can share the same parent without modifying it.

Each stage runs inside one Docker container containing the Python trainer and its Unity Player child. Training and trial containers share the DAG's Compose project and concurrency limit. A stage's `total_timesteps` is its additional training budget; PPO finishes whole rollouts, so it may exceed the requested count and does not promise a fixed number of episodes.

### Training workflow

```text
             Training node configuration
                       |
                       v
             Fresh policy or parent checkpoint
                       |
                       v
                 Run manager
                       |
                       v
            Docker Compose project
                (one per DAG run)
             Read-only checkpoint mounts
                 /    |    \
                v     v     v
             Train 1 Train 2 Train 3   ... shared C slots; refill on exit
                |     |     |
             Prepare independent environments
             Apply each branch's scene parameters
             Create or resume Python policy
             Collect experience + update policy
             Save checkpoint + export ONNX
                |     |     |
                 \    |    /
                       v
             Publish each node's artifacts
                       |
              +--------+--------+
              |                 |
              v                 v
       Next training stage   Test suite nodes
       (checkpoint input)    (ONNX inputs)
              |                 |
              v                 v
       Continue curriculum   Run trials + save evidence
                                |
                                v
                    Planned aggregation + final report
```

### Existing framework components

#### Unity

- `QaTrainingBootstrap`: selects the assigned build scene, requires exactly one enabled training adapter, and publishes readiness with the node identity, session, and listening port.
- `QaTrainingAdapter`: developer extension point for applying assignment parameters and starting the training host through `ConfigureAndStart(string assignmentJson)`.
- `RlEnvironmentBehaviour`: the Unity RL library's environment contract for reset, observation collection, action application/completion, reward, and episode termination.
- `RlTrainingHost`: serves the Python trainer over loopback TCP and dispatches environment callbacks on Unity's main thread. Automatic startup is disabled so the adapter can configure the environment first.

#### Python

- `qa-run-training`: owns the Player process, resolves fresh/resumed trainer settings, waits for readiness, invokes training and export, and publishes completed artifacts.
- `UnityGymEnv`: the Unity RL library's Gymnasium bridge for observations, actions, rewards, resets, and episode endings.
- `train(config_path)`: runs the library's PPO trainer, optionally resumes a full checkpoint, and saves intermediate checkpoints when configured plus the final model.
- `export_onnx(checkpoint, output)`: exports deterministic clipped policy actions for evaluation.
- `RunManager`: schedules ready stages, mounts parent checkpoints read-only, validates completed outputs, and releases dependent training or suite nodes.

### Developer-defined components

- Training scene, actor mechanics, initial state, reset behavior, and seed handling.
- Observation encoding and action mapping, with compatible shapes and meanings across curriculum stages and evaluation scenes.
- Reward function, action-completion rules, and episode-ending conditions.
- A concrete `QaTrainingAdapter` that reads `environment.parameters`, configures the environment, starts `RlTrainingHost`, and returns its listening port.
- Stage budgets, environment variants, trainer settings, and checkpoint dependencies in the DAG YAML.
- Evaluation environments and success criteria for comparing the resulting policies in test suites.

### Training lifecycle

The developer owns environment behavior and rewards. The Unity RL library owns the learning loop and checkpoint continuation. The QA platform owns container scheduling, Player startup/shutdown, artifact publication, and dependency handling.

```text
Run manager mounts assignment JSON + dedicated output directory
              + optional read-only parent checkpoint
                            |
                            v
+--------------------------------------------------------+
| ONE TRAINING CONTAINER                                 |
|                                                        |
|  qa-run-training resolves settings + starts Unity      |
|          |                                             |
|          v                                             |
|  Bootstrap selects scene -> adapter applies parameters |
|          |                                             |
|          v                                             |
|  Start RL host -> readiness file -> Python connects    |
|          |                                             |
|          v                                             |
|  Create/resume policy -> reset environment             |
|          |                                             |
|          v                                             |
|  Observe -> choose action -> apply -> wait             |
|          |                                             |
|          v                                             |
|  Return reward + done + next observation to Python     |
|          |                                             |
|          v                                             |
|  Reset ended episodes + collect rollouts + learn       |
|          |                  Repeat until stage budget  |
|          v                                             |
|  Save checkpoint.zip -> export/validate model.onnx     |
|          |                                             |
|          v                                             |
|  Stop Player -> publish artifacts.json -> exit         |
+--------------------------------------------------------+
                            |
                            v
           Platform saves logs + validates artifacts
                            |
                            v
              Release dependent training or suites
```

### QaTrainingAdapter flow

`QaTrainingAdapter` configures and starts the training environment. Its `ConfigureAndStart(string assignmentJson)` hook runs after the selected scene's Awake/OnEnable callbacks and before Start. The developer applies `environment.parameters`, calls `RlTrainingHost.StartTraining()` with automatic startup disabled, and returns the listening port. Setup errors throw and fail the stage. The bootstrap then publishes readiness; Python validates it before connecting to the host.

During training, `RlTrainingHost` dispatches the developer's `RlEnvironmentBehaviour` callbacks on Unity's main thread. The environment declares fixed `ObservationSize` and `ActionSize` values, supplies finite float observations, and accepts continuous actions in `[-1, 1]`. Python owns policy decisions and learning updates. Each action request advances the environment through `ApplyAction()` and waits until `IsActionRunning()` returns false before collecting its result.

```text
QaTrainingBootstrap selects scene -> finds one enabled QaTrainingAdapter
  -> ConfigureAndStart(assignmentJson) -> apply parameters -> start RL host
  -> return listening port -> publish readiness -> Python connects
  -> host announces ObservationSize and ActionSize -> initialize/resume Python policy
  -> Python requests reset -> ResetEpisode(seed) -> CollectObservation()
  -> Python selects action -> ApplyAction(action)
  -> IsActionRunning() is true -> wait across Unity updates
  -> IsActionRunning() is false -> CalculateReward(out isDone)
  -> CollectObservation() -> return observation, reward, and isDone to Python
  -> isDone is false -> continue episode with the next action
  -> isDone is true -> preserve terminal result -> require reset before another action
  -> Python collects rollouts and updates policy -> repeat until stage budget completes
  -> save checkpoint -> export policy -> stop Player -> publish completed artifacts
```

Reward and completion are calculated before the post-action observation. The environment preserves the terminal state until that observation has been returned, then resets only when Python requests the next episode. Python controls decision cadence through bridge requests; the environment determines how long each action takes. Readiness uses a file so checking startup does not consume the host's single trainer connection.

```text
Missing/ambiguous scene, invalid parameters, or host startup failure -> fail stage
Missing or mismatched readiness -> fail stage -> stop Unity
Action before reset or after episode completion -> reject action
Invalid action/observation/reward or environment callback error -> fail training
Disable training host or lose its connection -> trainer fails -> stop Player
Training, bridge, or export failure -> stop Unity -> retain partial files and logs
Cancellation -> stop and reap Unity -> retain partial output
Nonzero container exit or invalid published artifacts -> fail node -> block descendants
Independent DAG branches -> continue running
```

Startup allows 30 seconds. The trainer's socket timeout defaults to 300 seconds. Player cleanup terminates the child, waits two seconds, then kills and reaps it if necessary. Stage progression depends on successful budget completion and artifact validation; there are no performance gates or automatic retries.

### Training outputs and handoff

- `checkpoint.zip`: full policy, optimizer state, and saved training settings/counters for subsequent stages. Each child loads its own continuation from the read-only parent artifact.
- `model.onnx`: exported policy for suite evaluation; the host converts it to `.sentis` when needed and reuses that conversion within the DAG run.
- `training.yaml`: resolved trainer settings, including the mounted checkpoint path and stage output location.
- `artifacts.json`: completion manifest with node/run identity, parent reference, environment parameters, trainer settings, and published output paths.
- Training checkpoints, Unity/container logs, and node/job manifests remain available for inspection.

The current integration supports CPU PPO `MlpPolicy`, flat observations, and continuous actions in `[-1, 1]`. Resumed stages inherit saved optimizer/rollout settings; explicit conflicting settings are rejected, while the supported entropy override can change `ent_coef`. A test suite evaluates the exported models on its environment/agent matrix. Grading, aggregation, and the combined comparison report remain planned. See [the DAG guide](docs/training-dag.md) for the configuration and image contracts.

## Trials

### Definition

- Environment: one Unity scene with its setup/reset configuration. Different levels generated within that scene across episodes still belong to the same environment.
- Episode: the most basic unit, one attempt in an environment. The game calls `StartUnit()` once at the start and `EndUnit()` once at the end.
- Trial (simulation): runs X episodes for one environment and one agent.
- Suite: runs each of K agents in each of Y environments, giving Y trials per agent, Y x K trial runs, and X x Y x K episodes.

Each trial runs its X episodes inside one Docker container containing the Python runtime and its Unity Player child. One Docker Compose project groups all training and trial containers in the DAG.

### Trial workflow

```text
             Configuration / setup
                       |
                       v
             Suite model inputs become available
             Automated ONNX conversion
             Prepared .sentis files on host
                       |
                       v
                 Run manager
                       |
                       v
            Docker Compose project
                (one per DAG run)
             Read-only model mounts
                 /    |    \
                v     v     v
             Trial 1 Trial 2 Trial 3   ... shared C slots; refill on exit
                |     |     |
             Prepare independent environments
             Load assigned model + initialize CPU worker
             Adapter FixedUpdate drives active episode decisions
             Capture evidence
             Confirm saved output
                |     |     |
                 \    |    /
                       v
             Store results and traces
                       |
                       v
             Planned verifier execution + aggregation
                       |
                       v
             Planned reports and comparisons
```

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
Run manager mounts assignment JSON + dedicated output directory
                  + read-only prepared model
                            |
                            v
+--------------------------------------------------------+
| ONE TRIAL CONTAINER                                    |
|                                                        |
|  qa-run-trial opens listener + starts Unity            |
|          |                                             |
|          v                                             |
|  Bootstrap selects scene + one platform adapter        |
|          |                                             |
|          v                                             |
|  Python ready + model ready -> enable gameplay         |
|          |                                             |
|          v                                             |
|  StartUnit -> run test -> EndUnit                      |
|          |                  Repeat for X episodes      |
|          v                                             |
|  Evidence sent to Python -> append episodes.jsonl      |
|          |                                             |
|          v                                             |
|  Drain reports -> trial_complete                       |
|          |                                             |
|          v                                             |
|  Python validates count -> flush + fsync + close       |
|          |                                             |
|          v                                             |
|  trial_saved -> Unity exits -> Python exits            |
+--------------------------------------------------------+
                            |
                            v
              Platform collects the result
                            |
                            v
              Save logs + status -> free slot
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

## TODO

Training DAG execution, checkpoint branching, trial storage, and cleanup are implemented. Remaining work:

- [ ] Connect the existing `evaluate_unit()` to saved episode records for suite-level QA evaluation.
- [ ] Aggregate graded results per trial, environment, agent, and suite, including expected versus received episode counts.
- [ ] Report agent comparisons on the same environments, keeping execution failures separate from failed QA requirements.
- [ ] Build a reusable production runtime base image containing Python, the QA runtime, and Linux dependencies.
- [ ] Package each actual game's Unity build as a project image using the runtime base image.
- [ ] Migrate the dungeon game's observations, actions, evidence, and episode boundaries into `QaPlatformAdapter`.
