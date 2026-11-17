# Training and evaluation DAGs

Run `qa-run-suite workflow.yaml` with `qa-framework[platform]` installed on the Docker host. The name of the command is unchanged; its YAML format is now a DAG. See [the complete template](../suite.example.yaml) and [runnable sample](../examples/actor-control/training.yaml).

## Configuration

Top-level fields are `nodes`, `max_concurrent_containers`, `output_dir`, and `model_preparation`. Each node has a unique `id` (lowercase letter followed by letters, digits, hyphens or underscores; up to 63 characters), `type: train | suite`, and `image`. The image's entrypoint determines its runtime. There is no separate `depends_on`: artifact references define dependencies.

Training nodes contain:

- `environment: {id, scene, parameters}`: scene names must be unique in the Player build, or use full scene paths. Parameters are a finite JSON object interpreted by the developer's adapter.
- `trainer`: the settings below.
- Optional `checkpoint`: an existing host ZIP path, or `{node: producer, output: checkpoint}`. Omit it for a fresh policy.

Suite nodes contain `episodes_per_trial`, `environments`, and `agents`, preserving the existing matrix behavior. Each agent's `model` is an existing self-contained ONNX path or `{node: producer, output: onnx}`. A suite waits for all referenced producers. To evaluate successful branches independently of a failed sibling, declare a separate suite for each branch.

Paths resolve beside the YAML, except container paths in generated assignments. Output references need not exist during initial validation; the runner validates them after their producer exits successfully. Cycles, duplicate node/agent/environment IDs, unknown nodes, and incorrectly typed artifact references fail before launching work. Old flat YAML must be migrated by moving its image, environments, agents and episode count into a suite node and renaming its concurrency field.

| Trainer setting | Default / behavior |
| --- | --- |
| `total_timesteps` | Required positive budget for this stage; PPO may finish a rollout beyond it |
| `model`, `device` | `ppo`, `cpu`; other values rejected |
| `seed` | 0; seeds environment resets and fresh policy initialization |
| `checkpoint_freq` | 0, disabling intermediate checkpoints |
| `socket_timeout_seconds` | 300 |
| `learning_rate`, `gamma` | Fresh policy: 0.0003 and 0.99 |
| `n_steps`, `ppo.batch_size` | Fresh policy: 256 and 64 |
| `ent_coef` | Fresh policy: 0; explicit overrides supported when resuming |

Resumed optimizer/rollout settings inherit the full checkpoint. Explicit conflicting values are rejected, because the underlying library would otherwise ignore them. Omitted entropy settings inherit the saved coefficient. The environment seed may change; the checkpoint's saved policy/RNG behavior is still the library's continuation behavior. The platform owns `port`, `resume_from`, and trainer `output_dir`; these cannot be supplied under `trainer`. Training stages must keep the same observation/action shapes **and meanings**. The bridge validates shapes; developers are responsible for semantic compatibility.

## Training image and Unity integration

Install the QA package and the pinned Unity RL library's Python requirements in the image. Set `QA_RL_PYTHON` to the library's `PythonEnv` directory; the default is `/opt/unity-rl/PythonEnv`. Use:

```text
qa-run-training -- /app/Game -batchmode -nographics -logFile /qa/output/unity.log
```

The runner provides read-only assignment JSON through `QA_TRAIN_CONFIG`, a writable `QA_OUTPUT_DIR`, and, for continuation, the read-only checkpoint at `/qa/input/checkpoint.zip`. The full parent output directory is never exposed.

Build the training Player with a first scene containing `QaTrainingBootstrap`. Each supported training scene has exactly one enabled `QaTrainingAdapter`, an `RlEnvironmentBehaviour`, and an `RlTrainingHost` with **autoStart disabled**. The developer's adapter implements `ConfigureAndStart(string assignmentJson)`:

1. Deserialize a game-owned typed envelope containing `environment.parameters`.
2. Apply parameters to the environment after Awake/OnEnable, before Start.
3. Call `RlTrainingHost.StartTraining()`; throw if it fails.
4. Return `RlTrainingHost.Port`.

The bootstrap writes an atomic readiness file containing the port and session identity. Python waits up to 30 seconds without opening a probe connection, then calls the library's existing `train(config_path)` and `export_onnx(checkpoint, output)`. Training and export failures stop and reap the Player and produce no successful artifact manifest.

Training publishes `checkpoint.zip` and `model.onnx`, then `artifacts.json` after export validation and Player cleanup. The ZIP preserves optimizer/training state; ONNX contains deterministic clipped actions for evaluation. This integration uses PPO MlpPolicy with flat observations and continuous actions in [-1, 1].

The core QA Unity assembly does not depend on the RL assembly: the concrete training adapter supplies that integration. Trial-only consumers still need only the QA package. The actor sample includes both packages and pins Inference Engine 2.4.1 directly to align training exports with the trial converter/runtime.

## Scheduling and evaluation

One Docker Compose project owns the entire DAG. Training jobs and individual trials share `max_concurrent_containers`; suites themselves are groups, not additional containers. The ready queue allocates one job per ready node in turn and generates trials lazily. A stage succeeds only after its container exits zero and its required artifacts validate.

A failed training stage blocks descendants; independent branches continue. Suite trial failures do not cancel remaining trials. There are no retries, restart recovery, metric gates, weight merging, or GPU allocation. Ctrl+C/termination stops scheduling, cleans up this invocation's containers, and preserves output. Docker infrastructure failure stops the run.

A suite converts each required ONNX file when it becomes available, using the configured host Unity Editor and Inference Engine 2.4.1. Each distinct resolved source path converts once per invocation, shared across suites. Conversion reuses one isolated temporary project during the run, then removes it after its Editor exits and the DAG finishes. Conversion runs serially on the host and can delay scheduling/polling while existing containers continue running. Failed conversion fails the dependent suite; other branches remain eligible. No cross-run cache is used. Training-only DAGs do not require a host Editor.

Trial images keep `qa-run-trial`, `QA_TRIAL_CONFIG`, `QA_OUTPUT_DIR`, the read-only `/qa/model/agent.sentis` mount, and their existing saved-output handshake. Other environment/agent fields remain JSON pass-through for developer code.

## Saved output and planned reporting

```text
runs/<run-id>/
  manifest.json                 final node statuses and manifest locations
  status.jsonl                  lifecycle events, including stopped jobs
  models/conversion-*/          converted models and Editor logs
  nodes/<node-id>/
    manifest.json               config, dependencies, counts and published outputs
    jobs.jsonl                  completed/failed jobs and trial assignments
    assignment.json             training assignment
    output/                     training checkpoint, ONNX, config, logs and manifest
    trials/<trial-id>/           suite job assignment, container log, output/episodes.jsonl
```

Each job assignment records run/node identity; suite/trial IDs remain globally unique. The node config and training artifact manifest preserve parent references and environment parameters. Cancelled jobs retain their assignments, partial files and logs; `jobs.jsonl` lists jobs whose completion was collected, while `status.jsonl` records cleanup stops.

Exit codes are 0 for complete execution success, 1 for failed/blocked work or infrastructure failure, 2 for invalid YAML, and 130 for cancellation. Execution success is separate from QA correctness. Episode evidence remains ungraded. Connecting verifiers, aggregating per-suite results, and producing one cross-suite comparison report after DAG completion remain planned.
